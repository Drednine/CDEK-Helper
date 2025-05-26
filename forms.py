from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError
# from app import User # Старый импорт
from models import User # Новый импорт из models.py

class RegistrationForm(FlaskForm):
    # username = StringField('Имя пользователя',
    #                        validators=[DataRequired(), Length(min=2, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    # Пока не будем добавлять Email, чтобы упростить
    # email = StringField('Email',
    #                     validators=[DataRequired(), Email()])
    password = PasswordField('Пароль', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Подтвердите пароль',
                                     validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Зарегистрироваться')

    # def validate_username(self, username):
    #     user = User.query.filter_by(username=username.data).first()
    #     if user:
    #         raise ValidationError('Это имя пользователя уже занято. Пожалуйста, выберите другое.')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('Этот email уже используется. Пожалуйста, выберите другой.')

class LoginForm(FlaskForm):
    # username = StringField('Имя пользователя',
    #                        validators=[DataRequired(), Length(min=2, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    # email = StringField('Email',
    #                     validators=[DataRequired(), Email()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    remember = BooleanField('Запомнить меня')
    submit = SubmitField('Войти')

class OzonShopForm(FlaskForm):
    shop_name = StringField('Название магазина', validators=[DataRequired(), Length(max=150)])
    client_id = StringField('Client ID (Ozon)', validators=[DataRequired(), Length(max=100)])
    api_key = StringField('API Key (Ozon)', validators=[DataRequired(), Length(max=200)])
    warehouse_name = StringField('Название склада Ozon (для rFBS, например rFBS_Москва_Ховрино)', 
                                 default='rFBS', validators=[Length(max=100)])
    submit = SubmitField('Сохранить магазин Ozon')

class CdekAccountForm(FlaskForm):
    account_name = StringField('Название аккаунта CDEK', validators=[DataRequired(), Length(max=150)])
    client_id = StringField('Client ID (CDEK)', validators=[DataRequired(), Length(max=100)])
    client_secret = StringField('Client Secret (CDEK)', validators=[DataRequired(), Length(max=100)])
    submit = SubmitField('Сохранить аккаунт CDEK') 