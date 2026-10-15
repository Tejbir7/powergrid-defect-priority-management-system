from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_bcrypt import Bcrypt
from flask_mail import Mail
from flask_login import LoginManager
from apscheduler.schedulers.background import BackgroundScheduler

# Decoupled Flask extensions instances
db = SQLAlchemy()
csrf = CSRFProtect()
bcrypt = Bcrypt()
mail = Mail()
login_manager = LoginManager()
scheduler = BackgroundScheduler()
