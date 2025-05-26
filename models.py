from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# Инициализация SQLAlchemy здесь, чтобы избежать циклических импортов
db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    
    ozon_shops = db.relationship('OzonShop', backref='owner', lazy=True, cascade="all, delete-orphan")
    cdek_accounts = db.relationship('CdekAccount', backref='owner', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"User('{self.email}')"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class OzonShop(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shop_name = db.Column(db.String(150), nullable=False)
    client_id = db.Column(db.String(100), nullable=False)
    api_key = db.Column(db.String(200), nullable=False) # Ключи могут быть длинными
    warehouse_name = db.Column(db.String(100), nullable=True, default="rFBS")
    is_default = db.Column(db.Boolean, default=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def __repr__(self):
        return f"OzonShop('{self.shop_name}', UserID: {self.user_id})"

class CdekAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_name = db.Column(db.String(150), nullable=False)
    client_id = db.Column(db.String(100), nullable=False)
    client_secret = db.Column(db.String(100), nullable=False)
    is_default = db.Column(db.Boolean, default=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def __repr__(self):
        return f"CdekAccount('{self.account_name}', UserID: {self.user_id})" 