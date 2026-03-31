from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, date

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='operador')
    ventas = db.relationship('Venta', backref='usuario', lazy=True)

class Alumno(db.Model):
    __tablename__ = 'alumnos'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    dni = db.Column(db.String(20), unique=True, nullable=False)
    telefono = db.Column(db.String(20))
    contacto_emergencia = db.Column(db.String(100))
    telefono_emergencia = db.Column(db.String(20))
    fecha_inicio = db.Column(db.Date, default=date.today)
    fecha_vencimiento = db.Column(db.Date)
    ultimo_pago = db.Column(db.Date)
    tipo_clase = db.Column(db.String(50))
    valor_cuota = db.Column(db.Float, default=15000.0)
    forma_pago = db.Column(db.String(50))
    morosidad = db.Column(db.Boolean, default=False)
    activo = db.Column(db.Boolean, default=True)
    asistencia = db.Column(db.Integer, default=0)
    clases_totales = db.Column(db.Integer, default=0)
    clases_restantes = db.Column(db.Integer, default=0)
    
    asistencias = db.relationship('AsistenciaClase', backref='alumno_rel', lazy=True, cascade="all, delete-orphan")

class Clase(db.Model):
    __tablename__ = 'clases'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    dia = db.Column(db.String(20))
    hora = db.Column(db.String(10))
    capacidad = db.Column(db.Integer, default=20)
    asistencias = db.relationship('AsistenciaClase', backref='clase_rel', lazy=True, cascade="all, delete-orphan")

    @property
    def asistentes_hoy(self):
        """Calcula cuántos alumnos asistieron a esta clase el día de hoy"""
        return len([a for a in self.asistencias if a.fecha == date.today()])

class AsistenciaClase(db.Model):
    __tablename__ = 'asistencia_clases'
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('alumnos.id'), nullable=False)
    clase_id = db.Column(db.Integer, db.ForeignKey('clases.id'), nullable=False)
    fecha = db.Column(db.Date, default=date.today)

class Producto(db.Model):
    __tablename__ = 'productos'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    precio = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)
    ventas = db.relationship('Venta', backref='producto_rel', lazy=True)

class Venta(db.Model):
    __tablename__ = 'ventas'
    id = db.Column(db.Integer, primary_key=True)
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id'))
    producto_nombre = db.Column(db.String(100)) 
    monto = db.Column(db.Float, nullable=False)
    cantidad = db.Column(db.Integer, default=1)
    fecha = db.Column(db.DateTime, default=datetime.now)
    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'))