from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, date
from sqlalchemy import func

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='operador')  # admin, operador

class Alumno(db.Model):
    __tablename__ = 'alumnos'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    dni = db.Column(db.String(20), unique=True, nullable=False)
    telefono = db.Column(db.String(20))
    contacto_emergencia = db.Column(db.String(100))
    telefono_emergencia = db.Column(db.String(20))
    fecha_inicio = db.Column(db.Date, default=date.today)
    fecha_vencimiento = db.Column(db.Date) # Columna que faltaba
    ultimo_pago = db.Column(db.Date) # Columna que faltaba
    tipo_clase = db.Column(db.String(50))
    valor_cuota = db.Column(db.Float, default=15000)
    forma_pago = db.Column(db.String(50))
    clases_totales = db.Column(db.Integer, default=0)
    clases_restantes = db.Column(db.Integer, default=0)
    asistencia = db.Column(db.Integer, default=0)
    morosidad = db.Column(db.Boolean, default=False)
    activo = db.Column(db.Boolean, default=True)
    notas = db.Column(db.Text)
    fecha_baja = db.Column(db.Date)
    motivo_baja = db.Column(db.String(200))

class Clase(db.Model):
    __tablename__ = 'clases'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    dia = db.Column(db.String(20))
    hora = db.Column(db.String(10))
    capacidad = db.Column(db.Integer, default=20)
    asistentes = db.relationship('AsistenciaClase', backref='clase', lazy=True)

class AsistenciaClase(db.Model):
    __tablename__ = 'asistencia_clases'
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('alumnos.id'), nullable=False)
    clase_id = db.Column(db.Integer, db.ForeignKey('clases.id'), nullable=False)
    fecha = db.Column(db.Date, default=date.today)
    alumno = db.relationship('Alumno', backref='asistencias')

class Producto(db.Model):
    __tablename__ = 'productos'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    precio = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)

class Venta(db.Model):
    __tablename__ = 'ventas'
    id = db.Column(db.Integer, primary_key=True)
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id'), nullable=False)
    producto_nombre = db.Column(db.String(100))
    cantidad = db.Column(db.Integer, default=1)
    monto = db.Column(db.Float, nullable=False)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)
    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'))