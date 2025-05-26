from flask import Flask, render_template, jsonify, send_file, request, session, Response, redirect, url_for, flash
import requests
import pandas as pd
from datetime import datetime, timedelta
import io
import time
import zipfile
import os
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate
from flask_bcrypt import Bcrypt
from forms import RegistrationForm, LoginForm, OzonShopForm, CdekAccountForm
from models import db, User, OzonShop, CdekAccount

app = Flask(__name__)

# --- Конфигурация секрета и URI базы данных ---
# SECRET_KEY читается из переменной окружения, с fallback для локальной разработки
app.secret_key = os.environ.get('SECRET_KEY', 'your_fallback_secret_key_for_local_development_123!@#')

# Конфигурация URI базы данных
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    # Railway предоставляет DATABASE_URL как postgres://, SQLAlchemy ожидает postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
else:
    # Локальная разработка с SQLite
    # Создаем абсолютный путь к файлу site.db в папке instance
    instance_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance')
    if not os.path.exists(instance_path):
        os.makedirs(instance_path)
    db_path = os.path.join(instance_path, 'site.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False # Отключаем отслеживание модификаций

# --- Инициализация расширений Flask ---
db.init_app(app) # Теперь db инициализируется ПОСЛЕ установки URI
migrate = Migrate(app, db)
bcrypt = Bcrypt(app)

# --- Конфигурация Flask-Login ---
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Пожалуйста, войдите, чтобы получить доступ к этой странице.'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Старые глобальные переменные для API ключей (БУДУТ УДАЛЕНЫ/ЗАМЕНЕНЫ) ---
# OZON_CLIENT_ID = "2854913" 
# OZON_API_KEY = "f7638a53-9ec8-46c2-94e2-1e6836c8a34e"
# OZON_API_URL = "https://api-seller.ozon.ru/v3/posting/fbs/list" # Останется как константа
# OZON_ACCOUNT_NAME = "от А до Я" # Будет браться из OzonShop.shop_name или пользователь сам задаст
# CDEK_CLIENT_ID = "adyIEz5PEGgfKYM44dyFTWZ6n09vFYv4"
# CDEK_CLIENT_SECRET = "F5XYn3pmgNJ4nxBoKATRXpagmJjWF1wh"

# --- API Endpoint константы (остаются) ---
OZON_FBS_LIST_URL = "https://api-seller.ozon.ru/v3/posting/fbs/list"
CDEK_TOKEN_URL = "https://api.cdek.ru/v2/oauth/token"
CDEK_API_BASE_URL = "https://api.cdek.ru/v2"
MAX_CDEK_ORDERS_PER_BATCH = 100 
CDEK_POLLING_ATTEMPTS = 15 
CDEK_POLLING_INTERVAL_SECONDS = 3

# --- Функции API (потребуют модификации для получения credentials) ---

def get_active_ozon_shop():
    if not current_user.is_authenticated:
        return None
    # Пытаемся найти магазин по умолчанию, иначе первый попавшийся
    shop = OzonShop.query.filter_by(user_id=current_user.id, is_default=True).first()
    if not shop:
        shop = OzonShop.query.filter_by(user_id=current_user.id).first()
    return shop

def get_active_cdek_account():
    if not current_user.is_authenticated:
        return None
    account = CdekAccount.query.filter_by(user_id=current_user.id, is_default=True).first()
    if not account:
        account = CdekAccount.query.filter_by(user_id=current_user.id).first()
    return account

def get_cdek_access_token(): # Теперь использует активный аккаунт СДЭК
    cdek_account = get_active_cdek_account()
    if not cdek_account:
        print("CDEK Auth Error: No active CDEK account found for current user.")
        return None # Или возбуждать исключение/возвращать ошибку

    # Используем ID аккаунта CDEK для уникальности ключа сессии токена
    token_session_key = f'cdek_token_info_{cdek_account.id}'
    token_info = session.get(token_session_key)
    
    if token_info and token_info['expires_at'] > time.time() + 60:
        return token_info['access_token']

    payload = {
        'grant_type': 'client_credentials',
        'client_id': cdek_account.client_id,
        'client_secret': cdek_account.client_secret
    }
    response_obj = None
    try:
        response_obj = requests.post(CDEK_TOKEN_URL, data=payload, timeout=10)
        response_obj.raise_for_status()
        data = response_obj.json()
        access_token = data['access_token']
        expires_in = data.get('expires_in', 3600)
        session[token_session_key] = {
            'access_token': access_token,
            'expires_at': time.time() + expires_in
        }
        return access_token
    except requests.exceptions.RequestException as e:
        err_msg = f"CDEK Auth Error for account {cdek_account.account_name}: {e}"
        if response_obj is not None: err_msg += f" | Response: {response_obj.text[:200]}"
        print(err_msg)
        return None
    except KeyError as e:
        err_msg = f"CDEK Auth Key Error for account {cdek_account.account_name}: {e}"
        if response_obj is not None: err_msg += f" | Response: {response_obj.text[:200]}"
        print(err_msg)
        return None

def process_cdek_label_request_for_chunk(track_numbers_chunk):
    access_token = get_cdek_access_token()
    if not access_token:
        return None, f"Failed to get CDEK access token for chunk: {', '.join(track_numbers_chunk[:3])}..."

    headers_json = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    headers_pdf = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/pdf"
    }

    print_request_url = f"{CDEK_API_BASE_URL}/print/barcodes"
    orders_payload = [{"cdek_number": tn} for tn in track_numbers_chunk]
    
    payload = {
        "orders": orders_payload,
        "copy_count": 1,
        "format": "A6"
    }
    
    chunk_descriptor = track_numbers_chunk[0] if len(track_numbers_chunk) == 1 else f"{track_numbers_chunk[0]}... (total {len(track_numbers_chunk)})"
    print(f"CDEK_BATCH_STEP1: Requesting print generation for chunk starting with {chunk_descriptor} with {len(track_numbers_chunk)} orders. Format: A6.")

    response_obj_step1 = None
    try:
        response_obj_step1 = requests.post(print_request_url, headers=headers_json, json=payload, timeout=30)
        print(f"CDEK_BATCH_STEP1: Response status for chunk {chunk_descriptor}: {response_obj_step1.status_code}")

        if response_obj_step1.status_code == 202:
            response_data_step1 = response_obj_step1.json()
            if response_data_step1.get("entity") and response_data_step1["entity"].get("uuid"):
                batch_print_request_uuid = response_data_step1["entity"]["uuid"]
                print(f"CDEK_BATCH_STEP1: Print request accepted for chunk {chunk_descriptor}. Batch Print UUID: {batch_print_request_uuid}")

                if response_data_step1.get("requests") and isinstance(response_data_step1["requests"], list):
                    all_sub_requests_valid = True
                    for i, req_info in enumerate(response_data_step1["requests"]):
                        tn_for_log = track_numbers_chunk[i] if i < len(track_numbers_chunk) else "N/A"
                        state = req_info.get('state')
                        errors = req_info.get('errors')
                        print(f"CDEK_BATCH_STEP1_SUB_INFO for {tn_for_log}: SubRequest_State: {state}, SubRequest_Errors: {errors}")
                        if state == 'INVALID' or errors:
                            all_sub_requests_valid = False
                    if not all_sub_requests_valid:
                         print(f"CDEK_BATCH_WARNING: One or more sub-requests in chunk {chunk_descriptor} were marked as INVALID or had errors by CDEK on initial POST.")

                print(f"CDEK_BATCH_STEP2: Polling for readiness of Batch Print UUID: {batch_print_request_uuid} (Chunk: {chunk_descriptor}).")
                status_check_url = f"{CDEK_API_BASE_URL}/print/barcodes/{batch_print_request_uuid}"
                
                current_status_code = None # Инициализация current_status_code
                for attempt in range(CDEK_POLLING_ATTEMPTS):
                    print(f"CDEK_BATCH_STEP2: Polling attempt {attempt + 1}/{CDEK_POLLING_ATTEMPTS} for {batch_print_request_uuid}...")
                    poll_response_obj = None
                    try:
                        poll_response_obj = requests.get(status_check_url, headers=headers_json, timeout=15)
                        poll_response_obj.raise_for_status()
                        poll_data = poll_response_obj.json()
                        
                        entity_statuses = poll_data.get("entity", {}).get("statuses", [])
                        if entity_statuses and isinstance(entity_statuses, list):
                            current_status_code = entity_statuses[-1].get("code")

                        print(f"CDEK_BATCH_STEP2: Current status for {batch_print_request_uuid}: {current_status_code}")

                        if current_status_code == "READY":
                            print(f"CDEK_BATCH_STEP2: Batch {batch_print_request_uuid} is READY!")
                            download_url = f"{CDEK_API_BASE_URL}/print/barcodes/{batch_print_request_uuid}.pdf"
                            print(f"CDEK_BATCH_STEP3: Downloading PDF from {download_url} for chunk {chunk_descriptor}")
                            response_obj_step3 = requests.get(download_url, headers=headers_pdf, timeout=60)
                            
                            print(f"CDEK_BATCH_STEP3: PDF download response status for {batch_print_request_uuid}: {response_obj_step3.status_code}")

                            if response_obj_step3.status_code == 200 and 'application/pdf' in response_obj_step3.headers.get('Content-Type', '').lower():
                                print(f"CDEK_BATCH_STEP3: Successfully fetched PDF for chunk {chunk_descriptor} (Batch UUID: {batch_print_request_uuid})")
                                return response_obj_step3.content, None
                            else:
                                err_text_step3 = response_obj_step3.text[:200] if response_obj_step3.text else "No response text"
                                error_message_step3 = f"Failed to download PDF for chunk {chunk_descriptor} (UUID: {batch_print_request_uuid}). Status: {response_obj_step3.status_code}. Content-Type: {response_obj_step3.headers.get('Content-Type')}. Response: {err_text_step3}"
                                print(error_message_step3)
                                return None, error_message_step3
                        
                        elif current_status_code in ["INVALID", "REMOVED"]:
                            error_msg_poll = f"Polling for chunk {chunk_descriptor} (UUID: {batch_print_request_uuid}) failed. Status: {current_status_code}. Full statuses: {entity_statuses}"
                            print(error_msg_poll)
                            return None, error_msg_poll

                    except requests.exceptions.HTTPError as e_poll:
                        err_text_poll = e_poll.response.text[:200] if e_poll.response else "No poll response text"
                        print(f"CDEK_BATCH_STEP2: HTTP error during polling attempt {attempt + 1} for {batch_print_request_uuid}: {e_poll.response.status_code} - {err_text_poll}. Retrying if attempts left.")
                    except requests.exceptions.RequestException as e_poll_req:
                        print(f"CDEK_BATCH_STEP2: Request exception during polling attempt {attempt + 1} for {batch_print_request_uuid}: {e_poll_req}. Retrying if attempts left.")
                    except ValueError as e_poll_json: 
                        resp_text_poll = poll_response_obj.text[:200] if poll_response_obj else "No poll response object"
                        print(f"CDEK_BATCH_STEP2: JSON Decode Error during polling for {batch_print_request_uuid}: {e_poll_json}. Response: {resp_text_poll}. Retrying.")

                    if attempt < CDEK_POLLING_ATTEMPTS - 1:
                        time.sleep(CDEK_POLLING_INTERVAL_SECONDS)
                    else: 
                        timeout_error = f"Polling timeout for chunk {chunk_descriptor} (UUID: {batch_print_request_uuid}). Status remained {current_status_code} after {CDEK_POLLING_ATTEMPTS} attempts."
                        print(timeout_error)
                        return None, timeout_error
                return None, f"Polling loop ended unexpectedly for chunk {chunk_descriptor} (UUID: {batch_print_request_uuid})."

            else: 
                err_text_step1_json = response_obj_step1.text[:200]
                error_message_step1 = f"Print request accepted (202) for chunk {chunk_descriptor}, but no entity.uuid in response. Response: {err_text_step1_json}"
                print(error_message_step1)
                return None, error_message_step1
        
        elif response_obj_step1.status_code == 400: 
            response_data_step1 = {} 
            try: response_data_step1 = response_obj_step1.json() 
            except: pass 
            
            error_details_msg = "No specific error details in JSON response."
            if response_data_step1.get("requests") and isinstance(response_data_step1["requests"], list):
                errors_from_requests = []
                for req_idx, req_item in enumerate(response_data_step1["requests"]):
                    if req_item.get("errors"):
                        errors_from_requests.append(f"Order {track_numbers_chunk[req_idx] if req_idx < len(track_numbers_chunk) else req_idx}: {req_item.get('errors')}")
                if errors_from_requests: error_details_msg = "Details from 'requests': " + "; ".join(errors_from_requests)
            elif response_data_step1.get("alerts") and isinstance(response_data_step1["alerts"], list): 
                 error_details_msg = "Details from 'alerts': " + str(response_data_step1["alerts"][:3]) 
            elif response_data_step1.get("errors") and isinstance(response_data_step1["errors"], list): 
                 error_details_msg = "Details from 'errors': " + str(response_data_step1["errors"][:3])

            raw_text_preview = response_obj_step1.text[:200] if response_obj_step1.text else "No response text"
            error_message_step1 = f"CDEK Print Request (Step 1) HTTP 400 Error for chunk {chunk_descriptor}. {error_details_msg}. Raw: {raw_text_preview}"
            print(error_message_step1)
            return None, error_message_step1
        else: 
            err_text_step1 = response_obj_step1.text[:200]
            error_message_step1 = f"CDEK Print Request (Step 1) HTTP Error for chunk {chunk_descriptor}: {response_obj_step1.status_code} - {err_text_step1}"
            print(error_message_step1)
            return None, error_message_step1
            
    except requests.exceptions.RequestException as e: 
        error_msg = f"CDEK Print Request (Step 1) Request Error for chunk {chunk_descriptor}: {e}"
        if response_obj_step1 is not None: error_msg += f" | Response: {str(response_obj_step1.text)[:200]}"
        print(error_msg)
        return None, error_msg 
    except ValueError as e: 
        resp_text = response_obj_step1.text[:200] if response_obj_step1 else "No response object for step 1"
        print(f"CDEK Print Request (Step 1) JSON Decode Error for chunk {chunk_descriptor}: {e}. Response text: {resp_text}")
        return None, f"JSON decode error in print step 1 for chunk {chunk_descriptor}"

def get_ozon_awaiting_deliver_orders(): # Теперь использует активный магазин Ozon
    active_shop = get_active_ozon_shop()
    if not active_shop:
        msg = "Пожалуйста, сначала добавьте и/или выберите магазин Ozon в настройках аккаунта."
        flash(msg, "warning")
        return {"postings": [], "error": msg}, None, msg, 403

    client_id = active_shop.client_id
    api_key = active_shop.api_key
    warehouse_name_filter = active_shop.warehouse_name if active_shop.warehouse_name else "rFBS"

    print(f"Fetching Ozon orders for shop: {active_shop.shop_name} (User ID: {current_user.id}), Warehouse: {warehouse_name_filter}")
    all_postings_data = []
    offset = 0
    limit = 100 
    now = datetime.now()
    since = (now - timedelta(days=30)).isoformat() + "Z"
    to = now.isoformat() + "Z"

    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    current_error = None

    while True:
        payload = {
            "dir": "ASC",
            "filter": {"since": since, "status": "awaiting_deliver", "to": to},
            "limit": limit, "offset": offset,
            "with": {"analytics_data": False, "barcodes": False, "financial_data": False}
        }
        response_obj = None
        try:
            response_obj = requests.post(OZON_FBS_LIST_URL, headers=headers, json=payload, timeout=10)
            response_obj.raise_for_status()
            data_from_ozon = response_obj.json()
        except requests.exceptions.HTTPError as e:
            err_text = e.response.text[:200] if e.response else ""
            current_error = f"OZON_LIST HTTP Error for shop {active_shop.shop_name}: {e.response.status_code} - {err_text}"
            print(current_error)
            break
        except requests.exceptions.RequestException as e:
            current_error = f"OZON_LIST Request Exception for shop {active_shop.shop_name}: {e}"
            print(current_error)
            break
        except Exception as e: 
            current_error = f"OZON_LIST General Error for shop {active_shop.shop_name}: {str(e)}"
            print(current_error)
            break
        
        postings = data_from_ozon.get("result", {}).get("postings", [])
        if not postings:
            if offset == 0 and not all_postings_data and not current_error:
                current_error = f"OZON_LIST: No orders found for shop {active_shop.shop_name} or unexpected API response."
            break

        for posting in postings:
            delivery_method = posting.get("delivery_method", {})
            warehouse_value_from_api = delivery_method.get("warehouse", "")
            if warehouse_value_from_api != warehouse_name_filter:
                continue
            
            ozon_tracking_number = posting.get("tracking_number", "")
            if not ozon_tracking_number:
                print(f"OZON_LIST: Skipping posting {posting.get('posting_number')} as it has no tracking_number for CDEK.")
                continue

            order_date_str = ""
            if posting.get("in_process_at"):
                try:
                    dt_obj = datetime.fromisoformat(posting["in_process_at"].replace("Z", "+00:00"))
                    order_date_str = dt_obj.strftime("%d.%m.%Y")
                except ValueError:
                    order_date_str = posting["in_process_at"]

            for product in posting.get("products", []):
                all_postings_data.append({
                    "Магазин": active_shop.shop_name,
                    "Дата заказа": order_date_str,
                    "Номер отправления": posting.get("posting_number", ""),
                    "Артикул": product.get("offer_id", ""),
                    "Наименование товара": product.get("name", ""),
                    "Количество": product.get("quantity", 0),
                    "Трек-номер": ozon_tracking_number, 
                    "Склад": warehouse_value_from_api,
                    "4 Большие цифры": ozon_tracking_number[-4:] if ozon_tracking_number else ""
                })
        
        if len(postings) < limit: break
        offset += limit

    if not all_postings_data and not current_error:
        current_error = f"OZON_LIST: No 'awaiting_deliver' orders found for warehouse '{warehouse_name_filter}' in shop '{active_shop.shop_name}' within 30 days."
    
    if all_postings_data:
        all_postings_data.sort(key=lambda x: x["4 Большие цифры"])
        print(f"OZON: Processing done for shop {active_shop.shop_name}. Total items: {len(all_postings_data)}")
        return {"postings": all_postings_data, "error": current_error}, active_shop.shop_name, current_error, 200
    else:
        return {"postings": [], "error": current_error if current_error else "Нет данных для отображения"}, active_shop.shop_name, current_error if current_error else "Нет данных для отображения", 200 if not current_error else 404


# --- Маршруты ---
@app.route('/')
@login_required
def index():
    # active_shop = get_active_ozon_shop() # Это уже проверяется в get_ozon_awaiting_deliver_orders
    
    data_dict, shop_name_used, error_from_func, status_code = get_ozon_awaiting_deliver_orders()

    template_error_message = None # Инициализация переменной для сообщения в шаблоне

    if status_code == 403: # Магазин Ozon не настроен
        # Flash-сообщение уже установлено функцией get_ozon_awaiting_deliver_orders.
        # В шаблон передаем None, чтобы он отобразил свое стандартное сообщение "Нет заказов..."
        template_error_message = None 
    elif status_code != 200: # Другие ошибки при получении заказов
        if error_from_func: # Убедимся, что есть сообщение для flash
            flash(f"Ошибка при получении заказов Ozon: {error_from_func}", "danger")
        template_error_message = error_from_func # Передаем ошибку в шаблон
    else: # Успех (status_code == 200)
        # Даже при успехе, функция могла вернуть некритическую ошибку (например, если не найдено заказов)
        template_error_message = data_dict.get("error")

    return render_template(
        'ozon_orders.html',
        orders=data_dict.get("postings", []), # Безопасное получение списка заказов
        ozon_account_name=shop_name_used if shop_name_used else current_user.email,
        error_message=template_error_message # Сообщение для отображения в области контента
    )

@app.route('/get_cdek_labels', methods=['POST'])
@login_required
def get_cdek_labels_route():
    active_cdek_account = get_active_cdek_account()
    if not active_cdek_account:
        return jsonify({"success": False, "message": "Не настроен или не выбран аккаунт СДЭК.", "errors": [{"ozon_track_chunk": "N/A", "error":"Аккаунт СДЭК не настроен"}] }), 400
    data = request.get_json()
    ozon_tracking_numbers = data.get('ozon_tracking_numbers')

    if not ozon_tracking_numbers:
        return jsonify({"error": "Не выбраны трек-номера Ozon."}), 400
    
    ozon_tracking_numbers = [tn for tn in ozon_tracking_numbers if tn and tn.strip()]
    if not ozon_tracking_numbers:
        return jsonify({"error": "Список выбранных трек-номеров пуст или содержит только пустые значения."}), 400

    print(f"CDEK_ROUTE (User: {current_user.email}, Account: {active_cdek_account.account_name}): Received request for {len(ozon_tracking_numbers)} Ozon tracking numbers for printing. Max batch size: {MAX_CDEK_ORDERS_PER_BATCH}.")
    
    chunks = [
        ozon_tracking_numbers[i:i + MAX_CDEK_ORDERS_PER_BATCH] 
        for i in range(0, len(ozon_tracking_numbers), MAX_CDEK_ORDERS_PER_BATCH)
    ]
    print(f"CDEK_ROUTE: Processing {len(chunks)} chunk(s).")

    processed_pdf_data = [] 
    errors = []

    for i, chunk in enumerate(chunks):
        print(f"CDEK_ROUTE: Processing chunk {i + 1}/{len(chunks)} with {len(chunk)} orders.")
        pdf_content, error_label = process_cdek_label_request_for_chunk(chunk)
        
        if error_label:
            chunk_descriptor_err = chunk[0] if len(chunk) == 1 else f"chunk starting with {chunk[0]} ({len(chunk)} orders)"
            errors.append({"ozon_track_chunk": chunk_descriptor_err, "error": f"Ошибка получения этикеток для пачки: {error_label}"})
        
        if pdf_content:
            if len(chunk) == 1:
                filename_hint = f"cdek_label_{chunk[0].replace('/', '-')}.pdf"
            else:
                timestamp_part = datetime.now().strftime('%H%M%S%f')[:-3] 
                filename_hint = f"cdek_labels_batch_{i+1}_{len(chunk)}orders_{timestamp_part}.pdf"
            
            processed_pdf_data.append({
                "content": pdf_content,
                "filename": filename_hint,
                "original_tracks_in_chunk": chunk 
            })
        elif not error_label: 
             chunk_descriptor_err = chunk[0] if len(chunk) == 1 else f"chunk starting with {chunk[0]} ({len(chunk)} orders)"
             errors.append({"ozon_track_chunk": chunk_descriptor_err, "error": "Не удалось получить PDF для пачки по неизвестной причине."})


    if not processed_pdf_data and errors: 
        return jsonify({"success": False, "errors": errors, "message": "Не удалось получить ни одной этикетки для выбранных заказов."}), 400
    
    if not processed_pdf_data and not errors: 
         return jsonify({"success": False, "message": "Нет данных для этикеток и нет ошибок (неожиданная ситуация)."}), 400

    if len(processed_pdf_data) == 1 and not errors: 
        single_pdf_item = processed_pdf_data[0]
        print(f"CDEK_ROUTE: Sending single PDF file: {single_pdf_item['filename']}")
        return Response(
            single_pdf_item['content'],
            mimetype='application/pdf',
            headers={'Content-Disposition': f'inline;filename="{single_pdf_item["filename"]}"'},
            status=200
        )
    
    if processed_pdf_data:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED, False) as zip_file: 
            for pdf_item in processed_pdf_data:
                zip_file.writestr(pdf_item["filename"], pdf_item["content"])
        
        zip_buffer.seek(0)
        zip_filename = f"cdek_labels_archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        
        num_files_in_zip = len(zip_file.namelist())

        if num_files_in_zip == 0 and errors: 
             return jsonify({"success": False, "errors": errors, "message": "Не удалось добавить файлы в архив, только ошибки."}), 500
        elif num_files_in_zip == 0 and not errors: 
             return jsonify({"success": False, "message": "Архив пуст, нет данных для этикеток."}), 500

        print(f"CDEK_ROUTE: Prepared ZIP file {zip_filename} with {num_files_in_zip} PDF(s). Total errors during processing: {len(errors)}")
        
        return Response(
            zip_buffer.getvalue(),
            mimetype='application/zip',
            headers={'Content-Disposition': f'attachment; filename="{zip_filename}"'},
            status=200
        )

    return jsonify({"success": False, "errors": errors, "message": "Неизвестная ошибка при формировании этикеток."}), 500


@app.route('/download_ozon_excel')
@login_required
def download_ozon_excel():
    data_dict, shop_name, error_message_from_func, status_code = get_ozon_awaiting_deliver_orders()
    if error_message_from_func:
        flash(f"Ошибка при получении данных для Excel: {error_message_from_func}", "danger")
        return redirect(url_for('index'))

    if not data_dict:
        flash("Нет данных для экспорта в Excel.", "info")
        return redirect(url_for('index'))

    df = pd.DataFrame(data_dict["postings"])
    output = io.BytesIO()
    try:
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Ожидают отправки')
    except Exception as e:
        print(f"Error writing Excel file: {e}")
        flash(f"Произошла ошибка при формировании Excel файла: {e}", "error")
        return redirect(url_for('index'))
            
    output.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shop_name_part = shop_name.replace(" ", "_")[:20] if shop_name else "shop"
    filename = f"ozon_orders_{shop_name_part}_{timestamp}.xlsx"
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True, 
        download_name=filename
    )

# --- Новые маршруты для аутентификации и управления ---
@app.route("/register", methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
        user = User(email=form.email.data, password_hash=hashed_password) # Используем email
        db.session.add(user)
        db.session.commit()
        flash('Ваша учетная запись создана! Теперь вы можете войти.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', title='Регистрация', form=form)

@app.route("/login", methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first() # Используем email
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            login_user(user, remember=form.remember.data)
            next_page = request.args.get('next')
            flash('Вход выполнен успешно!', 'success')
            return redirect(next_page) if next_page else redirect(url_for('index'))
        else:
            flash('Не удалось войти. Пожалуйста, проверьте email и пароль.', 'danger')
    return render_template('login.html', title='Вход', form=form)

@app.route("/logout")
def logout():
    logout_user()
    flash('Вы вышли из системы.', 'info')
    return redirect(url_for('login'))

@app.route("/account")
@login_required
def account():
    ozon_shops = OzonShop.query.filter_by(user_id=current_user.id).order_by(OzonShop.shop_name).all()
    cdek_accounts = CdekAccount.query.filter_by(user_id=current_user.id).order_by(CdekAccount.account_name).all()
    return render_template('account.html', title='Мой аккаунт', 
                           ozon_shops=ozon_shops,
                           cdek_accounts=cdek_accounts) # Передаем аккаунты CDEK

@app.route("/add_ozon_shop", methods=['GET', 'POST'])
@login_required
def add_ozon_shop():
    form = OzonShopForm()
    if form.validate_on_submit():
        new_shop = OzonShop(
            shop_name=form.shop_name.data,
            client_id=form.client_id.data,
            api_key=form.api_key.data,
            warehouse_name=form.warehouse_name.data if form.warehouse_name.data else "rFBS",
            user_id=current_user.id
        )
        # Проверка, является ли этот магазин первым для пользователя
        existing_shops_count = OzonShop.query.filter_by(user_id=current_user.id).count()
        if existing_shops_count == 0:
            new_shop.is_default = True
            flash('Магазин Ozon добавлен и установлен по умолчанию, так как это ваш первый магазин.', 'success')
        else:
            flash('Магазин Ozon успешно добавлен.', 'success')
        
        db.session.add(new_shop)
        db.session.commit()
        return redirect(url_for('account'))
    return render_template('add_edit_ozon_shop.html', title='Добавить магазин Ozon', form=form, legend='Новый магазин Ozon')

@app.route("/edit_ozon_shop/<int:shop_id>", methods=['GET', 'POST'])
@login_required
def edit_ozon_shop(shop_id):
    shop = OzonShop.query.get_or_404(shop_id)
    if shop.owner != current_user:
        flash('У вас нет прав для редактирования этого магазина.', 'danger')
        return redirect(url_for('account'))
    
    form = OzonShopForm()
    if form.validate_on_submit():
        shop.shop_name = form.shop_name.data
        shop.client_id = form.client_id.data
        shop.api_key = form.api_key.data
        shop.warehouse_name = form.warehouse_name.data if form.warehouse_name.data else "rFBS"
        db.session.commit()
        flash('Данные магазина Ozon обновлены.', 'success')
        return redirect(url_for('account'))
    elif request.method == 'GET':
        form.shop_name.data = shop.shop_name
        form.client_id.data = shop.client_id
        form.api_key.data = shop.api_key
        form.warehouse_name.data = shop.warehouse_name
    return render_template('add_edit_ozon_shop.html', title='Редактировать магазин Ozon', form=form, legend=f'Редактировать: {shop.shop_name}')

@app.route("/delete_ozon_shop/<int:shop_id>", methods=['POST']) # Используем POST для безопасности
@login_required
def delete_ozon_shop(shop_id):
    shop = OzonShop.query.get_or_404(shop_id)
    if shop.owner != current_user:
        flash('У вас нет прав для удаления этого магазина.', 'danger')
        return redirect(url_for('account'))
    
    # Если удаляемый магазин был по умолчанию, и есть другие магазины, делаем первый из оставшихся по умолчанию
    was_default = shop.is_default
    db.session.delete(shop)
    db.session.commit()
    flash(f'Магазин Ozon "{shop.shop_name}" удален.', 'success')

    if was_default:
        remaining_shop = OzonShop.query.filter_by(user_id=current_user.id).first()
        if remaining_shop:
            remaining_shop.is_default = True
            db.session.commit()
            flash(f'Магазин "{remaining_shop.shop_name}" был установлен по умолчанию.', 'info')
            
    return redirect(url_for('account'))

@app.route("/set_default_ozon_shop/<int:shop_id>", methods=['POST'])
@login_required
def set_default_ozon_shop(shop_id):
    shop_to_set_default = OzonShop.query.get_or_404(shop_id)
    if shop_to_set_default.owner != current_user:
        flash('У вас нет прав на это действие.', 'danger')
        return redirect(url_for('account'))

    # Сначала снимаем флаг is_default со всех магазинов Ozon текущего пользователя
    current_user_shops = OzonShop.query.filter_by(user_id=current_user.id).all()
    for s in current_user_shops:
        s.is_default = False
    
    # Устанавливаем флаг is_default для выбранного магазина
    shop_to_set_default.is_default = True
    db.session.commit()
    flash(f'Магазин "{shop_to_set_default.shop_name}" установлен по умолчанию.', 'success')
    return redirect(url_for('account'))

# --- Маршруты для управления аккаунтами CDEK ---
@app.route("/add_cdek_account", methods=['GET', 'POST'])
@login_required
def add_cdek_account():
    form = CdekAccountForm()
    if form.validate_on_submit():
        new_account = CdekAccount(
            account_name=form.account_name.data,
            client_id=form.client_id.data,
            client_secret=form.client_secret.data,
            user_id=current_user.id
        )
        existing_accounts_count = CdekAccount.query.filter_by(user_id=current_user.id).count()
        if existing_accounts_count == 0:
            new_account.is_default = True
            flash('Аккаунт CDEK добавлен и установлен по умолчанию, так как это ваш первый аккаунт.', 'success')
        else:
            flash('Аккаунт CDEK успешно добавлен.', 'success')
        
        db.session.add(new_account)
        db.session.commit()
        return redirect(url_for('account'))
    return render_template('add_edit_cdek_account.html', title='Добавить аккаунт CDEK', form=form, legend='Новый аккаунт CDEK')

@app.route("/edit_cdek_account/<int:account_id>", methods=['GET', 'POST'])
@login_required
def edit_cdek_account(account_id):
    cdek_account = CdekAccount.query.get_or_404(account_id)
    if cdek_account.owner != current_user:
        flash('У вас нет прав для редактирования этого аккаунта CDEK.', 'danger')
        return redirect(url_for('account'))
    
    form = CdekAccountForm()
    if form.validate_on_submit():
        cdek_account.account_name = form.account_name.data
        cdek_account.client_id = form.client_id.data
        cdek_account.client_secret = form.client_secret.data
        db.session.commit()
        flash('Данные аккаунта CDEK обновлены.', 'success')
        return redirect(url_for('account'))
    elif request.method == 'GET':
        form.account_name.data = cdek_account.account_name
        form.client_id.data = cdek_account.client_id
        form.client_secret.data = cdek_account.client_secret
    return render_template('add_edit_cdek_account.html', title='Редактировать аккаунт CDEK', form=form, legend=f'Редактировать: {cdek_account.account_name}')

@app.route("/delete_cdek_account/<int:account_id>", methods=['POST'])
@login_required
def delete_cdek_account(account_id):
    cdek_account = CdekAccount.query.get_or_404(account_id)
    if cdek_account.owner != current_user:
        flash('У вас нет прав для удаления этого аккаунта CDEK.', 'danger')
        return redirect(url_for('account'))
    
    was_default = cdek_account.is_default
    account_name_deleted = cdek_account.account_name
    db.session.delete(cdek_account)
    db.session.commit()
    flash(f'Аккаунт CDEK "{account_name_deleted}" удален.', 'success')

    if was_default:
        remaining_account = CdekAccount.query.filter_by(user_id=current_user.id).first()
        if remaining_account:
            remaining_account.is_default = True
            db.session.commit()
            flash(f'Аккаунт CDEK "{remaining_account.account_name}" был установлен по умолчанию.', 'info')
            
    return redirect(url_for('account'))

@app.route("/set_default_cdek_account/<int:account_id>", methods=['POST'])
@login_required
def set_default_cdek_account(account_id):
    account_to_set_default = CdekAccount.query.get_or_404(account_id)
    if account_to_set_default.owner != current_user:
        flash('У вас нет прав на это действие.', 'danger')
        return redirect(url_for('account'))

    current_user_cdek_accounts = CdekAccount.query.filter_by(user_id=current_user.id).all()
    for acc in current_user_cdek_accounts:
        acc.is_default = False
    
    account_to_set_default.is_default = True
    db.session.commit()
    flash(f'Аккаунт CDEK "{account_to_set_default.account_name}" установлен по умолчанию.', 'success')
    return redirect(url_for('account'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
