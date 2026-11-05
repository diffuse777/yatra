from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort, session, Response, send_from_directory
import random
import json
import logging
import csv
import io
from datetime import datetime, time, timedelta, date
from enum import Enum
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Integer, and_, or_, func
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException, NotFound, BadRequest, Unauthorized, Forbidden, MethodNotAllowed, RequestTimeout, RequestEntityTooLarge, TooManyRequests, InternalServerError, ServiceUnavailable, GatewayTimeout
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
import os
import time
import base64
try:
    import requests
except ImportError:
    requests = None



def create_admin_user():
    from werkzeug.security import generate_password_hash
    
    # Check if admin user already exists
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(
            username='admin',
            password_hash=generate_password_hash('admin123', method='pbkdf2:sha256'),
            is_admin=True
        )
        db.session.add(admin)
        db.session.commit()
        print("Admin user created successfully!")
    else:
        print("Admin user already exists")
    return admin

app = Flask(__name__)
# Use environment variable for SECRET_KEY if available, otherwise generate one
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
# For Vercel serverless, use /tmp directory for SQLite, otherwise use instance folder
if os.environ.get('VERCEL'):
    db_path = '/tmp/bus_system.db'
else:
    # Ensure instance folder exists for local development
    _base_dir = os.path.abspath(os.path.dirname(__file__))
    _instance_dir = os.path.join(_base_dir, 'instance')
    os.makedirs(_instance_dir, exist_ok=True)
    db_path = os.path.join(_instance_dir, 'bus_system.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PER_PAGE'] = 10
db = SQLAlchemy(app)
migrate = Migrate(app, db)
csrf = CSRFProtect(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@app.after_request
def add_cors_headers(response):
    """CORS: allow Campus GPT (any origin) to call /api/* endpoints."""
    if request.path.startswith('/api/'):
        response.headers['Access-Control-Allow-Origin']  = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
        response.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    return response
def register_template_filters(app):
    from jinja2.runtime import Undefined
    from markupsafe import Markup
    app.jinja_env.filters['fromjson'] = json.loads
    def safe_json(obj):
        if obj is None or isinstance(obj, Undefined):
            return Markup('null')
        if hasattr(obj, 'to_dict'):
            obj = obj.to_dict()
        try:
            dumped = json.dumps(obj, default=str)
            # Keep JS/HTML safe and prevent auto-escape turning quotes into &quot;
            dumped = (
                dumped.replace('<', '\\u003c')
                .replace('>', '\\u003e')
                .replace('&', '\\u0026')
            )
            return Markup(dumped)
        except TypeError:
            return Markup('null')
    def number_format(value, format="{:,.0f}"):
        if value is None or isinstance(value, Undefined):
            return "0"
        try:
            num = float(value)
            return format.format(num)
        except (ValueError, TypeError):
            return str(value) if value is not None else "0"
    app.jinja_env.filters['number_format'] = number_format
    app.jinja_env.filters['tojson'] = safe_json
register_template_filters(app)
class LogActionType(Enum):
    LOGIN = "Login"
    LOGOUT = "Logout"
    CREATE = "Create"
    UPDATE = "Update"
    DELETE = "Delete"
    ACCESS = "Access"
    ERROR = "Error"
    SYSTEM = "System"
    SECURITY = "Security"
class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(45), unique=True, nullable=False, index=True)
    first_seen = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    user_agent = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    logs = db.relationship('Log', backref='device', lazy=True)

    def __repr__(self):
        return f"<Device {self.ip_address}>"

class Log(db.Model):
    __table_args__ = (
        db.UniqueConstraint('timestamp', 'action_type', 'user_id', 'description', 'device_id', name='unique_log_entry'),
    )
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    action_type = db.Column(db.String(20), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id', ondelete='SET NULL'), nullable=True, index=True)
    endpoint = db.Column(db.String(100), nullable=True)
    method = db.Column(db.String(10), nullable=True)
    description = db.Column(db.Text, nullable=False)
    details = db.Column(db.JSON, nullable=True)
    user = db.relationship('User', backref=db.backref('logs', lazy='dynamic', cascade='all, delete-orphan'))
    def __repr__(self):
        return f"<Log {self.id}: {self.action_type} - {self.description[:50]}>"
    def to_dict(self):
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(),
            'action_type': self.action_type,
            'user': self.user.username if self.user else 'System',
            'ip_address': self.ip_address,
            'endpoint': self.endpoint,
            'method': self.method,
            'description': self.description,
            'details': self.details or {}
        }
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    buses = db.relationship('Bus', backref='driver', lazy=True)
    events = db.relationship('Event', backref='user', lazy=True)
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'is_admin': self.is_admin,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S')
        }
class BusRoute(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    route_number = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    start_point = db.Column(db.String(100), nullable=False)
    end_point = db.Column(db.String(100), nullable=False)
    distance = db.Column(db.Float, nullable=True)
    estimated_duration = db.Column(db.Integer, nullable=True)
    stops = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    buses = db.relationship('Bus', backref='route', lazy=True)
    def to_dict(self):
        return {
            'id': self.id,
            'route_number': self.route_number,
            'name': self.name,
            'start_point': self.start_point,
            'end_point': self.end_point,
            'distance': self.distance,
            'estimated_duration': self.estimated_duration,
            'stops': json.loads(self.stops) if self.stops else [],
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S')
        }
class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    details = db.Column(db.Text, nullable=True)
    location = db.Column(db.String(100), nullable=True)
    bus_id = db.Column(db.Integer, db.ForeignKey('bus.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    def to_dict(self):
        return {
            'id': self.id,
            'event_type': self.event_type,
            'timestamp': self.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'details': self.details,
            'location': self.location,
            'bus_id': self.bus_id,
            'user_id': self.user_id
        }
class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    passenger_name = db.Column(db.String(100), nullable=False)
    passenger_phone = db.Column(db.String(20), nullable=False)
    service_number = db.Column(db.String(20), nullable=False)
    origin = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    booking_time = db.Column(db.DateTime, default=datetime.utcnow)
    travel_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default='confirmed')
    seat_number = db.Column(db.String(10), nullable=True)
    fare = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    def to_dict(self):
        return {
            'id': self.id,
            'passenger_name': self.passenger_name,
            'service_number': self.service_number,
            'origin': self.origin,
            'destination': self.destination,
            'booking_time': self.booking_time.strftime('%Y-%m-%d %H:%M:%S'),
            'travel_date': self.travel_date.strftime('%Y-%m-%d'),
            'status': self.status,
            'seat_number': self.seat_number,
            'fare': self.fare
        }
class BusService(db.Model):
    """Model representing a bus service with routes and stops."""
    __tablename__ = 'bus_service'
    
    id = db.Column(db.Integer, primary_key=True)
    service_number = db.Column(db.String(20), unique=True, nullable=False)
    bus_number = db.Column(db.String(20), nullable=False)
    origin = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    departure_time = db.Column(db.Time, nullable=False)
    arrival_time = db.Column(db.Time, nullable=False)
    origin_departure_time = db.Column(db.Time, nullable=True)
    destination_arrival_time = db.Column(db.Time, nullable=True)
    _via_routes = db.Column('via_routes', db.JSON, nullable=True, default=list)
    _stops = db.Column('stops', db.JSON, nullable=True, default=list)
    route_description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    bus = db.relationship('Bus', backref='service', uselist=False)

    def __init__(self, **kwargs):
        """Initialize a new BusService instance with default values for JSON fields."""
        super(BusService, self).__init__(**kwargs)
        # Ensure JSON fields are properly initialized
        if self._via_routes is None:
            self._via_routes = []
        if self._stops is None:
            self._stops = []

    @property
    def via_routes(self):
        """Get the via_routes as a list."""
        return self._via_routes or []

    @via_routes.setter
    def via_routes(self, value):
        """Set via_routes, ensuring it's a list."""
        self._via_routes = list(value) if value is not None else []

    @property
    def stops(self):
        """Get the stops as a list."""
        return self._stops or []

    @stops.setter
    def stops(self, value):
        """Set stops, ensuring it's a list and properly formatted."""
        if value is None:
            self._stops = []
        else:
            # Ensure each stop has the required fields
            self._stops = [{
                'name': str(stop.get('name', '')),
                'arrival': str(stop.get('arrival', '')),
                'departure': str(stop.get('departure', ''))
            } for stop in value if isinstance(stop, dict)]

    def to_dict(self):
        """Convert the BusService instance to a dictionary."""
        return {
            'id': self.id,
            'service_number': self.service_number,
            'bus_number': self.bus_number,
            'origin': self.origin,
            'destination': self.destination,
            'departure_time': self.departure_time.strftime('%H:%M') if self.departure_time else None,
            'arrival_time': self.arrival_time.strftime('%H:%M') if self.arrival_time else None,
            'via_routes': self.via_routes,
            'stops': self.stops,
            'route_description': self.route_description,
            'is_active': self.is_active,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None
        }
class Bus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bus_number = db.Column(db.String(20), unique=True, nullable=False)
    license_plate = db.Column(db.String(20), unique=True, nullable=True)
    capacity = db.Column(db.Integer, nullable=False, default=50)
    current_passengers = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='active')
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    current_location = db.Column(db.String(100), nullable=True)
    route_id = db.Column(db.Integer, db.ForeignKey('bus_route.id'), nullable=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    service_id = db.Column(db.Integer, db.ForeignKey('bus_service.id'), nullable=True)
    def to_dict(self):
        return {
            'id': self.id,
            'bus_number': self.bus_number,
            'license_plate': self.license_plate,
            'capacity': self.capacity,
            'current_passengers': self.current_passengers,
            'status': self.status,
            'last_updated': self.last_updated.strftime('%Y-%m-%d %H:%M:%S'),
            'current_location': self.current_location,
            'occupancy_percentage': self.occupancy_percentage()
        }
    def occupancy_percentage(self):
        if self.capacity == 0:
            return 0
        return int((self.current_passengers / self.capacity) * 100)

class SpecialEvent(db.Model):
    """Model for managing special events and crowd demand"""
    __tablename__ = 'special_event'
    
    id = db.Column(db.Integer, primary_key=True)
    event_name = db.Column(db.String(200), nullable=False)
    event_description = db.Column(db.Text, nullable=True)
    location = db.Column(db.String(100), nullable=False)
    event_date = db.Column(db.Date, nullable=False)
    expected_demand = db.Column(db.Integer, default=100)  # Expected number of passengers
    route_origin = db.Column(db.String(100), nullable=False)  # Where people are coming from
    route_destination = db.Column(db.String(100), nullable=False)  # Where people are going
    additional_buses = db.Column(db.Integer, default=0)  # Number of extra buses added
    status = db.Column(db.String(20), default='pending')  # pending, active, completed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'event_name': self.event_name,
            'event_description': self.event_description,
            'location': self.location,
            'event_date': self.event_date.strftime('%Y-%m-%d') if self.event_date else None,
            'expected_demand': self.expected_demand,
            'route_origin': self.route_origin,
            'route_destination': self.route_destination,
            'additional_buses': self.additional_buses,
            'status': self.status,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None
        }

class BusStop(db.Model):
    """Model for bus stops with geo-coordinates"""
    __tablename__ = 'bus_stop'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(100), nullable=False)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    impact_zone = db.Column(db.String(50), nullable=True)  # e.g., 'university', 'airport', 'train_station'
    is_major_stop = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'location': self.location,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'impact_zone': self.impact_zone,
            'is_major_stop': self.is_major_stop
        }

class StopAnalytics(db.Model):
    """Historical passenger data at each stop"""
    __tablename__ = 'stop_analytics'
    
    id = db.Column(db.Integer, primary_key=True)
    stop_name = db.Column(db.String(100), nullable=False)
    stop_location = db.Column(db.String(100), nullable=False)
    date = db.Column(db.Date, nullable=False)
    hour = db.Column(db.Integer, nullable=False)  # 0-23
    passenger_count = db.Column(db.Integer, default=0)
    weekday = db.Column(db.String(20), nullable=True)  # Monday, Tuesday, etc.
    is_holiday = db.Column(db.Boolean, default=False)
    weather_condition = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'stop_name': self.stop_name,
            'stop_location': self.stop_location,
            'date': self.date.strftime('%Y-%m-%d') if self.date else None,
            'hour': self.hour,
            'passenger_count': self.passenger_count,
            'weekday': self.weekday,
            'is_holiday': self.is_holiday
        }

class CrowdPrediction(db.Model):
    """AI-generated crowd predictions for stops"""
    __tablename__ = 'crowd_prediction'
    
    id = db.Column(db.Integer, primary_key=True)
    stop_name = db.Column(db.String(100), nullable=False)
    stop_location = db.Column(db.String(100), nullable=False)
    prediction_date = db.Column(db.Date, nullable=False)
    prediction_hour = db.Column(db.Integer, nullable=False)  # 0-23
    predicted_passengers = db.Column(db.Integer, nullable=False)
    confidence_level = db.Column(db.Float, default=0.0)  # 0.0 to 1.0
    factors = db.Column(db.JSON, nullable=True)  # Factors affecting prediction
    recommendation = db.Column(db.Text, nullable=True)  # AI recommendation
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'stop_name': self.stop_name,
            'stop_location': self.stop_location,
            'prediction_date': self.prediction_date.strftime('%Y-%m-%d') if self.prediction_date else None,
            'prediction_hour': self.prediction_hour,
            'predicted_passengers': self.predicted_passengers,
            'confidence_level': self.confidence_level,
            'factors': self.factors,
            'recommendation': self.recommendation
        }

class PassengerRecommendation(db.Model):
    """Recommendations sent to passengers"""
    __tablename__ = 'passenger_recommendation'
    
    id = db.Column(db.Integer, primary_key=True)
    passenger_phone = db.Column(db.String(20), nullable=False)
    origin = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    recommended_time = db.Column(db.Time, nullable=True)
    recommended_stop = db.Column(db.String(100), nullable=True)
    reason = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='pending')  # pending, sent, viewed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'passenger_phone': self.passenger_phone,
            'origin': self.origin,
            'destination': self.destination,
            'recommended_time': self.recommended_time.strftime('%H:%M') if self.recommended_time else None,
            'recommended_stop': self.recommended_stop,
            'reason': self.reason,
            'status': self.status
        }

class AlertEvent(db.Model):
    """Event-triggered alerts for depot managers"""
    __tablename__ = 'alert_event'
    
    id = db.Column(db.Integer, primary_key=True)
    alert_type = db.Column(db.String(50), nullable=False)  # overcrowding, delay, event_impact
    stop_name = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(20), default='medium')  # low, medium, high, critical
    predicted_time = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='active')  # active, acknowledged, resolved
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'alert_type': self.alert_type,
            'stop_name': self.stop_name,
            'message': self.message,
            'severity': self.severity,
            'predicted_time': self.predicted_time.strftime('%Y-%m-%d %H:%M:%S') if self.predicted_time else None,
            'status': self.status,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None
        }

class TravelPlan(db.Model):
    """Passenger travel plans with multi-modal options"""
    __tablename__ = 'travel_plan'
    
    id = db.Column(db.Integer, primary_key=True)
    passenger_phone = db.Column(db.String(20), nullable=True)  # Optional - only for notifications
    passenger_name = db.Column(db.String(100), nullable=True)  # Optional - not required
    origin = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    travel_date = db.Column(db.Date, nullable=False)
    preferred_time = db.Column(db.Time, nullable=True)
    transport_mode = db.Column(db.String(20), nullable=False)  # bus, train, flight, multi_modal
    plan_details = db.Column(db.JSON, nullable=True)  # Full travel plan details
    weather_alert = db.Column(db.Text, nullable=True)
    crowd_prediction = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default='active')  # active, completed, cancelled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'passenger_phone': self.passenger_phone,
            'passenger_name': self.passenger_name,
            'origin': self.origin,
            'destination': self.destination,
            'travel_date': self.travel_date.strftime('%Y-%m-%d') if self.travel_date else None,
            'preferred_time': self.preferred_time.strftime('%H:%M') if self.preferred_time else None,
            'transport_mode': self.transport_mode,
            'plan_details': self.plan_details,
            'weather_alert': self.weather_alert,
            'crowd_prediction': self.crowd_prediction,
            'status': self.status
        }

class WeatherData(db.Model):
    """Weather data for cities/stops"""
    __tablename__ = 'weather_data'
    
    id = db.Column(db.Integer, primary_key=True)
    location = db.Column(db.String(100), nullable=False)
    date = db.Column(db.Date, nullable=False)
    temperature = db.Column(db.Float, nullable=True)
    condition = db.Column(db.String(50), nullable=True)  # sunny, rainy, foggy, cloudy
    humidity = db.Column(db.Float, nullable=True)
    wind_speed = db.Column(db.Float, nullable=True)
    forecast = db.Column(db.Text, nullable=True)
    advisory = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'location': self.location,
            'date': self.date.strftime('%Y-%m-%d') if self.date else None,
            'temperature': self.temperature,
            'condition': self.condition,
            'humidity': self.humidity,
            'wind_speed': self.wind_speed,
            'forecast': self.forecast,
            'advisory': self.advisory
        }

class TrainSchedule(db.Model):
    """Train schedule information"""
    __tablename__ = 'train_schedule'
    
    id = db.Column(db.Integer, primary_key=True)
    train_number = db.Column(db.String(20), nullable=False)
    train_name = db.Column(db.String(200), nullable=False)
    origin_station = db.Column(db.String(100), nullable=False)
    destination_station = db.Column(db.String(100), nullable=False)
    departure_time = db.Column(db.Time, nullable=False)
    arrival_time = db.Column(db.Time, nullable=False)
    days_of_operation = db.Column(db.String(50), nullable=True)  # Daily, Mon-Fri, etc.
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'train_number': self.train_number,
            'train_name': self.train_name,
            'origin_station': self.origin_station,
            'destination_station': self.destination_station,
            'departure_time': self.departure_time.strftime('%H:%M') if self.departure_time else None,
            'arrival_time': self.arrival_time.strftime('%H:%M') if self.arrival_time else None,
            'days_of_operation': self.days_of_operation,
            'is_active': self.is_active
        }

class FlightSchedule(db.Model):
    """Flight schedule information"""
    __tablename__ = 'flight_schedule'
    
    id = db.Column(db.Integer, primary_key=True)
    flight_number = db.Column(db.String(20), nullable=False)
    airline = db.Column(db.String(100), nullable=False)
    origin_airport = db.Column(db.String(100), nullable=False)
    destination_airport = db.Column(db.String(100), nullable=False)
    departure_time = db.Column(db.Time, nullable=False)
    arrival_time = db.Column(db.Time, nullable=False)
    days_of_operation = db.Column(db.String(50), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'flight_number': self.flight_number,
            'airline': self.airline,
            'origin_airport': self.origin_airport,
            'destination_airport': self.destination_airport,
            'departure_time': self.departure_time.strftime('%H:%M') if self.departure_time else None,
            'arrival_time': self.arrival_time.strftime('%H:%M') if self.arrival_time else None,
            'days_of_operation': self.days_of_operation,
            'is_active': self.is_active
        }

class TravelNotification(db.Model):
    """Intelligent notifications for passengers"""
    __tablename__ = 'travel_notification'
    
    id = db.Column(db.Integer, primary_key=True)
    passenger_phone = db.Column(db.String(20), nullable=False)
    notification_type = db.Column(db.String(50), nullable=False)  # delay, weather, crowd, connection
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    related_plan_id = db.Column(db.Integer, db.ForeignKey('travel_plan.id'), nullable=True)
    priority = db.Column(db.String(20), default='normal')  # low, normal, high, urgent
    status = db.Column(db.String(20), default='unread')  # unread, read, dismissed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'passenger_phone': self.passenger_phone,
            'notification_type': self.notification_type,
            'title': self.title,
            'message': self.message,
            'related_plan_id': self.related_plan_id,
            'priority': self.priority,
            'status': self.status,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None
        }

class PassengerPreference(db.Model):
    """Passenger travel preferences for personalized recommendations"""
    __tablename__ = 'passenger_preference'
    
    id = db.Column(db.Integer, primary_key=True)
    passenger_phone = db.Column(db.String(20), nullable=False)
    preferred_times = db.Column(db.JSON, nullable=True)  # Array of preferred hours
    preferred_stops = db.Column(db.JSON, nullable=True)  # Array of preferred stops
    travel_history = db.Column(db.JSON, nullable=True)  # Historical travel patterns
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'passenger_phone': self.passenger_phone,
            'preferred_times': self.preferred_times,
            'preferred_stops': self.preferred_stops,
            'travel_history': self.travel_history
        }

class LocalBus(db.Model):
    """Local bus services (state transport)"""
    __tablename__ = 'local_bus'
    
    id = db.Column(db.Integer, primary_key=True)
    bus_number = db.Column(db.String(50), nullable=False)
    route_number = db.Column(db.String(20), nullable=False)
    operator = db.Column(db.String(100), nullable=False)  # SETC, TNSTC, etc.
    origin = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    departure_time = db.Column(db.Time, nullable=False)
    arrival_time = db.Column(db.Time, nullable=False)
    via_stops = db.Column(db.JSON, nullable=True)  # Array of intermediate stops
    fare = db.Column(db.Float, nullable=True)
    bus_type = db.Column(db.String(50), nullable=True)  # Express, Superfast, Deluxe
    seat_availability = db.Column(db.Integer, default=0)  # Available seats
    total_seats = db.Column(db.Integer, default=50)
    current_location = db.Column(db.String(100), nullable=True)  # Real-time location
    status = db.Column(db.String(20), default='scheduled')  # scheduled, on_time, delayed, departed, arrived
    delay_minutes = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    @property
    def occupancy_percentage(self):
        """Calculate occupancy percentage"""
        if self.total_seats == 0:
            return 0
        occupied = self.total_seats - self.seat_availability
        return int((occupied / self.total_seats) * 100)
    
    def to_dict(self):
        return {
            'id': self.id,
            'bus_number': self.bus_number,
            'route_number': self.route_number,
            'operator': self.operator,
            'origin': self.origin,
            'destination': self.destination,
            'departure_time': self.departure_time.strftime('%H:%M') if self.departure_time else None,
            'arrival_time': self.arrival_time.strftime('%H:%M') if self.arrival_time else None,
            'via_stops': self.via_stops,
            'fare': self.fare,
            'bus_type': self.bus_type,
            'seat_availability': self.seat_availability,
            'total_seats': self.total_seats,
            'current_location': self.current_location,
            'status': self.status,
            'delay_minutes': self.delay_minutes,
            'occupancy_percentage': self.occupancy_percentage
        }

class PrivateOperator(db.Model):
    """Private bus operators (RedBus, etc.)"""
    __tablename__ = 'private_operator'
    
    id = db.Column(db.Integer, primary_key=True)
    operator_name = db.Column(db.String(100), nullable=False)  # KPN Travels, SRS Travels, etc.
    bus_number = db.Column(db.String(50), nullable=False)
    route_name = db.Column(db.String(200), nullable=False)
    origin = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    departure_time = db.Column(db.Time, nullable=False)
    arrival_time = db.Column(db.Time, nullable=False)
    via_stops = db.Column(db.JSON, nullable=True)
    fare = db.Column(db.Float, nullable=False)
    bus_type = db.Column(db.String(50), nullable=True)  # AC Sleeper, Non-AC Seater, etc.
    amenities = db.Column(db.JSON, nullable=True)  # WiFi, Charging, etc.
    seat_availability = db.Column(db.Integer, default=0)
    total_seats = db.Column(db.Integer, default=40)
    current_location = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default='available')  # available, running, delayed, cancelled
    delay_minutes = db.Column(db.Integer, default=0)
    rating = db.Column(db.Float, default=0.0)  # Operator rating
    platform_source = db.Column(db.String(50), nullable=True)  # redbus, abhi, makemytrip, etc.
    booking_url = db.Column(db.String(500), nullable=True)
    live_tracking = db.Column(db.Boolean, default=True)  # Live GPS tracking available
    duration = db.Column(db.String(20), nullable=True)  # Journey duration
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    @property
    def occupancy_percentage(self):
        """Calculate occupancy percentage"""
        if self.total_seats == 0:
            return 0
        occupied = self.total_seats - self.seat_availability
        return int((occupied / self.total_seats) * 100)
    
    def to_dict(self):
        return {
            'id': self.id,
            'operator_name': self.operator_name,
            'bus_number': self.bus_number,
            'route_name': self.route_name,
            'origin': self.origin,
            'destination': self.destination,
            'departure_time': self.departure_time.strftime('%H:%M') if self.departure_time else None,
            'arrival_time': self.arrival_time.strftime('%H:%M') if self.arrival_time else None,
            'via_stops': self.via_stops,
            'fare': self.fare,
            'bus_type': self.bus_type,
            'amenities': self.amenities,
            'seat_availability': self.seat_availability,
            'total_seats': self.total_seats,
            'current_location': self.current_location,
            'status': self.status,
            'delay_minutes': self.delay_minutes,
            'rating': self.rating,
            'platform_source': self.platform_source,
            'booking_url': self.booking_url,
            'live_tracking': self.live_tracking,
            'duration': self.duration,
            'occupancy_percentage': self.occupancy_percentage
        }

class RealTimeBusStatus(db.Model):
    """Real-time bus tracking status"""
    __tablename__ = 'realtime_bus_status'
    
    id = db.Column(db.Integer, primary_key=True)
    bus_id = db.Column(db.Integer, nullable=False)
    bus_type = db.Column(db.String(20), nullable=False)  # local, private, service
    current_location = db.Column(db.String(100), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    speed = db.Column(db.Float, nullable=True)  # km/h
    estimated_arrival = db.Column(db.DateTime, nullable=True)
    delay_minutes = db.Column(db.Integer, default=0)
    next_stop = db.Column(db.String(100), nullable=True)
    occupancy = db.Column(db.Integer, nullable=True)  # Current passengers
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'bus_id': self.bus_id,
            'bus_type': self.bus_type,
            'current_location': self.current_location,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'speed': self.speed,
            'estimated_arrival': self.estimated_arrival.strftime('%Y-%m-%d %H:%M:%S') if self.estimated_arrival else None,
            'delay_minutes': self.delay_minutes,
            'next_stop': self.next_stop,
            'occupancy': self.occupancy,
            'last_updated': self.last_updated.strftime('%Y-%m-%d %H:%M:%S') if self.last_updated else None
        }


class BusGPSTelemetry(db.Model):
    """Latest GPS fix from an on-board device (Arduino / ESP32) for a specific route."""
    __tablename__ = 'bus_gps_telemetry'

    id = db.Column(db.Integer, primary_key=True)
    route_key = db.Column(db.String(80), nullable=False, index=True)
    device_id = db.Column(db.String(64), nullable=False, default='bus1')
    bus_number = db.Column(db.String(40), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    speed_kmh = db.Column(db.Float, default=0.0)
    heading = db.Column(db.Float, nullable=True)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('route_key', 'device_id', name='uq_bus_gps_route_device'),
    )

    def to_live_dict(self, stale_seconds=120):
        """stale_seconds: mark live=False if last update is older than this (default 120s)."""
        now = datetime.utcnow()
        age = (now - self.last_updated).total_seconds() if self.last_updated else 99999
        return {
            'bus_number': self.bus_number,
            'device_id': self.device_id,
            'latitude': round(float(self.latitude), 7),
            'longitude': round(float(self.longitude), 7),
            'speed_kmh': round(float(self.speed_kmh or 0), 2),
            'heading': self.heading,
            'last_updated': self.last_updated.strftime('%Y-%m-%d %H:%M:%S UTC') if self.last_updated else None,
            'live': age <= stale_seconds,
            'seconds_since_update': int(age),
            'has_position': True,
        }


class EducationalInstitution(db.Model):
    """Educational institutions in the region"""
    __tablename__ = 'educational_institution'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    institution_type = db.Column(db.String(50), nullable=True)  # University, College, School
    location = db.Column(db.String(100), nullable=False)
    address = db.Column(db.Text, nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    nearest_bus_stop = db.Column(db.String(100), nullable=True)
    distance_to_bus_stop_km = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'institution_type': self.institution_type,
            'location': self.location,
            'address': self.address,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'nearest_bus_stop': self.nearest_bus_stop,
            'distance_to_bus_stop_km': self.distance_to_bus_stop_km
        }

class TravelRoute(db.Model):
    """Travel routes with step-by-step directions"""
    __tablename__ = 'travel_route'
    
    id = db.Column(db.Integer, primary_key=True)
    route_name = db.Column(db.String(200), nullable=False)
    origin = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    origin_lat = db.Column(db.Float, nullable=True)
    origin_lng = db.Column(db.Float, nullable=True)
    destination_lat = db.Column(db.Float, nullable=True)
    destination_lng = db.Column(db.Float, nullable=True)
    total_distance_km = db.Column(db.Float, nullable=True)
    estimated_time_minutes = db.Column(db.Integer, nullable=True)
    route_steps = db.Column(db.JSON, nullable=True)  # Array of step objects
    alternate_transport = db.Column(db.JSON, nullable=True)  # Auto, share van options
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'route_name': self.route_name,
            'origin': self.origin,
            'destination': self.destination,
            'origin_lat': self.origin_lat,
            'origin_lng': self.origin_lng,
            'destination_lat': self.destination_lat,
            'destination_lng': self.destination_lng,
            'total_distance_km': self.total_distance_km,
            'estimated_time_minutes': self.estimated_time_minutes,
            'route_steps': self.route_steps,
            'alternate_transport': self.alternate_transport
        }

class Landmark(db.Model):
    """Landmarks and rest points (tea stalls, ATMs, petrol bunks)"""
    __tablename__ = 'landmark'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    landmark_type = db.Column(db.String(50), nullable=False)  # tea_stall, atm, petrol_bunk, restaurant, etc.
    location = db.Column(db.String(100), nullable=False)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    description = db.Column(db.Text, nullable=True)
    is_rest_point = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'landmark_type': self.landmark_type,
            'location': self.location,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'description': self.description,
            'is_rest_point': self.is_rest_point
        }

# Create missing tables (e.g. new models) without dropping existing data
with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
def create_tables():
    with app.app_context():
        db.drop_all()
        db.create_all()
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            admin = User(username='admin', is_admin=True)
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
@app.before_request
def before_request():
    if request.endpoint and not request.endpoint.startswith('static'):
        # Allow public passenger pages and root without authentication
        if request.path == '/' or request.path == '/favicon.ico' or \
           request.path.startswith('/passengers') or request.path.startswith('/apassengers') or \
           request.path.startswith('/weather') or request.path.startswith('/train-map') or \
           request.path.startswith('/tourism') or request.path.startswith('/travel-guide') or \
           request.path.startswith('/travel-planner') or \
           request.path.startswith('/api/'):
            return None
        if current_user.is_authenticated:
            log_action(
                action_type=LogActionType.ACCESS,
                description=f"Accessed {request.endpoint}",
                user_id=current_user.id,
                ip_address=request.remote_addr,
                endpoint=request.endpoint,
                method=request.method,
                details={
                    'path': request.path,
                    'args': dict(request.args),
                    'form': dict(request.form) if request.form else None
                }
            )
        if not current_user.is_authenticated and request.endpoint not in ['login', 'static', 'logout', 'create-admin', 'index', 'passengers_home', 'weather_page', 'train_map', 'tourism_page', 'travel_guide', 'travel_planner']:
            return redirect(url_for('login'))
@app.route('/create-admin')
def create_admin_account():
    admin = create_admin_user()
    if admin:
        return 'Admin user created successfully!', 200
    return 'Admin user already exists', 200
@app.route('/favicon.ico')
def favicon():
    """Avoid 404 → error pages that extend base.html (fixes console 500 on anonymous /favicon.ico)."""
    static_dir = os.path.join(app.root_path, 'static')
    fp = os.path.join(static_dir, 'favicon.ico')
    if os.path.isfile(fp):
        return send_from_directory(static_dir, 'favicon.ico', mimetype='image/vnd.microsoft.icon')
    # 1×1 transparent PNG — valid favicon for modern browsers
    return Response(
        base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
        ),
        mimetype='image/png',
        headers={'Cache-Control': 'public, max-age=86400'},
    )


@app.route('/')
def index():
    return redirect(url_for('passengers_home'))
@app.route('/admin')
@login_required
def admin_home():
    if not current_user.is_admin:
        return redirect(url_for('index'))
    return redirect(url_for('dashboard'))
@app.route('/my-ip')
def show_my_ip():
    """Route to display the visitor's IP address"""
    visitor_ip = request.remote_addr
    return f"Your IP address is: {visitor_ip}"
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        ip_address = request.remote_addr
        user_agent = request.headers.get('User-Agent', 'Unknown')
        log_action(
            action_type=LogActionType.LOGIN,
            description=f"Login attempt for username: {username}",
            ip_address=ip_address,
            endpoint=request.endpoint,
            method=request.method,
            details={
                'user_agent': user_agent,
                'success': False,
                'username': username
            }
        )
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            next_page = request.args.get('next')
            log_action(
                action_type=LogActionType.LOGIN,
                description=f"Successful login for {username}",
                user_id=user.id,
                ip_address=ip_address,
                endpoint=request.endpoint,
                method=request.method,
                details={
                    'user_agent': user_agent,
                    'success': True,
                    'is_admin': user.is_admin
                }
            )
            return redirect(next_page or url_for('dashboard'))
        else:
            log_action(
                action_type=LogActionType.SECURITY,
                description=f"Failed login attempt for username: {username}",
                ip_address=ip_address,
                endpoint=request.endpoint,
                method=request.method,
                details={
                    'user_agent': user_agent,
                    'success': False,
                    'username': username,
                    'reason': 'Invalid credentials'
                }
            )
            flash('Invalid username or password', 'danger')
    return render_template('login.html')
@app.route('/dashboard')
@login_required
def dashboard():
    if not current_user.is_admin:
        return redirect(url_for('index'))
    print("\n=== ALL SERVICES ===")
    all_services = BusService.query.all()
    for service in all_services:
        print(f"Service: {service.service_number} (ID: {service.id}) - Bus: {service.bus_number} - Active: {service.is_active}")
        if service.is_active and not Bus.query.filter_by(bus_number=service.bus_number).first():
            print(f"Creating missing bus: {service.bus_number}")
            bus = Bus(
                bus_number=service.bus_number,
                capacity=50,
                status='active',
                service_id=service.id
            )
            db.session.add(bus)
    db.session.commit()
    print("\n=== ALL BUSES AND THEIR SERVICES ===")
    all_buses = Bus.query.all()
    for bus in all_buses:
        service = BusService.query.filter_by(bus_number=bus.bus_number).first()
        print(f"Bus: {bus.bus_number} (ID: {bus.id}) - Service: {service.service_number if service else 'None'} - Active: {service.is_active if service else 'N/A'}")
    total_buses = db.session.query(Bus).join(
        BusService,
        and_(
            Bus.bus_number == BusService.bus_number,
            BusService.is_active == True
        )
    ).count()
    stmt = db.session.query(Bus).join(
        BusService,
        and_(
            Bus.bus_number == BusService.bus_number,
            BusService.is_active == True
        )
    )
    print("\n=== DEBUG: ACTIVE BUSES QUERY ===")
    print(str(stmt.statement.compile(compile_kwargs={"literal_binds": True})))
    print(f"Total active buses: {total_buses}")
    active_buses = db.session.query(Bus).join(
        BusService,
        and_(
            Bus.bus_number == BusService.bus_number,
            BusService.is_active == True
        )
    ).all()
    routes = BusRoute.query.options(db.joinedload(BusRoute.buses)).order_by(BusRoute.route_number).all()
    active_services = db.session.query(BusService).filter(
        BusService.is_active == True
    ).order_by(BusService.service_number).all()
    active_services_data = []
    for service in active_services:
        service_data = {
            'id': service.id,
            'service_number': service.service_number,
            'bus_number': service.bus_number,
            'origin': service.origin,
            'destination': service.destination,
            'origin_departure_time': service.origin_departure_time,
            'destination_arrival_time': service.destination_arrival_time,
            'via_routes': service.via_routes or [],
            'created_at': service.created_at,
            'updated_at': service.updated_at
        }
        active_services_data.append(service_data)
    total_capacity = sum(bus.capacity for bus in active_buses)
    total_passengers = sum(bus.current_passengers for bus in active_buses)
    avg_occupancy = int((total_passengers / total_capacity) * 100) if total_capacity > 0 else 0
    today = datetime.utcnow().date()
    
    # Get travel plans (not bookings - this is a planning/recommendation system)
    travel_plans_today = TravelPlan.query.filter(
        TravelPlan.travel_date == today,
        TravelPlan.status == 'active'
    ).count()
    
    # Use travel plans count instead of bookings for "Travel Plans Today"
    if travel_plans_today > 0:
        today_passengers = travel_plans_today
    else:
        # Use demo data if no travel plans yet
        today_passengers = random.randint(20, 50)
    
    buses_needing_attention = [bus for bus in active_buses
                             if bus.occupancy_percentage() > 80 or
                             bus.status == 'maintenance']
    return render_template('dashboard.html',
                         total_buses=total_buses,
                         today_passengers=today_passengers,
                         avg_occupancy=avg_occupancy,
                         buses_needing_attention=buses_needing_attention,
                         active_buses=active_buses,
                         active_services=active_services_data,
                         routes=routes)
@app.route('/profile')
@login_required
def user_profile():
    """User profile page"""
    user_stats = {
        'total_buses': len(current_user.buses) if current_user.buses else 0,
        'total_events': len(current_user.events) if current_user.events else 0,
        'account_age_days': (datetime.utcnow() - current_user.created_at).days if current_user.created_at else 0
    }
    
    # Get recent activity
    recent_logs = Log.query.filter_by(user_id=current_user.id).order_by(Log.timestamp.desc()).limit(10).all()
    
    return render_template('profile.html', 
                         user_stats=user_stats,
                         recent_logs=recent_logs)

@app.route('/logout')
@login_required
def logout():
    if current_user.is_authenticated:
        log_action(
            action_type=LogActionType.LOGOUT,
            description=f"User {current_user.username} logged out",
            user_id=current_user.id,
            ip_address=request.remote_addr,
            endpoint=request.endpoint,
            method=request.method,
            details={
                'user_agent': request.headers.get('User-Agent', 'Unknown')
            }
        )
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404
@app.errorhandler(500)
def internal_error(e):
    return render_template('500.html'), 500
def log_action(action_type, description, user_id=None, ip_address=None, endpoint=None, method=None, details=None):
    """
    Helper function to log actions to the database.
    Tracks unique devices by IP address and ensures logs are not duplicated.
    
    Args:
        action_type: Type of action (from LogActionType enum or string)
        description: Description of the action
        user_id: ID of the user performing the action (optional)
        ip_address: IP address of the requester (optional)
        endpoint: API endpoint being accessed (optional)
        method: HTTP method used (optional)
        details: Additional details as a dictionary (optional)
    
    Returns:
        Log: The created log entry or None if logging failed
    """
    try:
        # Convert action_type to string if it's an Enum
        if isinstance(action_type, LogActionType):
            action_type = action_type.value
            
        # Get current timestamp
        now = datetime.utcnow()
        
        # Get or create device record
        device = None
        if ip_address:
            device = Device.query.filter_by(ip_address=ip_address).first()
            if not device:
                device = Device(
                    ip_address=ip_address,
                    user_agent=request.headers.get('User-Agent')
                )
                db.session.add(device)
                db.session.flush()  # Get the device ID before commit
            else:
                # Update last_seen timestamp
                device.last_seen = now
        
        # Check for duplicate log entry within last minute
        duplicate_window = now - timedelta(seconds=60)
        
        query = Log.query.filter(
            Log.action_type == action_type,
            Log.description == description,
            Log.timestamp >= duplicate_window,
            Log.user_id == user_id,
            Log.device_id == (device.id if device else None)
        )
            
        if endpoint:
            query = query.filter(Log.endpoint == endpoint)
            
        if method:
            query = query.filter(Log.method == method)
            
        duplicate = query.first()
        if duplicate:
            return duplicate
            
        # Create new log entry
        log = Log(
            action_type=action_type,
            description=description,
            user_id=user_id,
            device_id=device.id if device else None,
            endpoint=endpoint,
            method=method,
            details=details
        )
        
        db.session.add(log)
        db.session.commit()
        return log
        
    except IntegrityError as e:
        db.session.rollback()
        app.logger.error(f"Integrity error logging action: {str(e)}")
        # Fallback to file logging if database logging fails
        app.logger.error(f"Action failed to log - Type: {action_type}, Desc: {description}, User: {user_id}, IP: {ip_address}")
        return None
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error logging action: {str(e)}")
        # Fallback to file logging if database logging fails
        try:
            with open('error_log.log', 'a') as f:
                f.write(f"{datetime.utcnow().isoformat()} - Failed to log action: {str(e)}\n")
                f.write(f"Action: {action_type}, Description: {description}, User: {user_id}, IP: {ip_address}\n\n")
        except Exception as inner_e:
            app.logger.critical(f"Critical logging failure: {str(inner_e)}")
        return None
        return None
@app.route('/logs')
@login_required
def logs():
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('dashboard'))
    
    # Get admin logs (all actions by admin users)
    admin_logs = Log.query.join(User).filter(
        User.is_admin == True
    ).order_by(Log.timestamp.desc()).limit(50).all()
    
    return render_template('logs/simple_logs.html',
                         admin_logs=admin_logs)

# Public Passenger Portal
@app.route('/apassengers')
@app.route('/passengers')
def passengers_home():
    from datetime import date as _d
    active_services = db.session.query(BusService).filter(BusService.is_active == True).order_by(BusService.service_number).all()
    return render_template('apassengers/index.html',
                           active_services=active_services,
                           today_date=_d.today().strftime('%Y-%m-%d'))

def get_weather_recommendations(weather_data, city_type='destination'):
    """Generate travel recommendations based on weather conditions."""
    if not weather_data:
        return []
    
    recommendations = []
    condition = weather_data.get('condition', '') or ''
    condition = condition.lower() if isinstance(condition, str) else ''
    
    # Ensure numeric values are not None before comparisons
    temp = weather_data.get('temperature')
    if temp is None:
        temp = 25
    else:
        try:
            temp = float(temp)
        except (ValueError, TypeError):
            temp = 25
    
    humidity = weather_data.get('humidity')
    if humidity is None:
        humidity = 50
    else:
        try:
            humidity = float(humidity)
        except (ValueError, TypeError):
            humidity = 50
    
    wind_speed = weather_data.get('wind_speed')
    if wind_speed is None:
        wind_speed = 0
    else:
        try:
            wind_speed = float(wind_speed)
        except (ValueError, TypeError):
            wind_speed = 0
    
    if condition in ['rain', 'drizzle', 'rainy']:
        recommendations.append({
            'type': 'warning',
            'icon': '🌧️',
            'message': f"Rain expected at {city_type}. Don't forget to pack an umbrella and waterproof bags."
        })
    elif condition in ['thunderstorm']:
        recommendations.append({
            'type': 'danger',
            'icon': '⛈️',
            'message': f"Severe weather alert! Thunderstorms expected. Consider rescheduling if possible."
        })
    elif temp > 35:
        recommendations.append({
            'type': 'warning',
            'icon': '🔥',
            'message': f"Very hot weather ({int(temp)}°C). Stay hydrated and wear light, breathable clothing."
        })
    elif temp < 15:
        recommendations.append({
            'type': 'info',
            'icon': '🧥',
            'message': f"Cool weather ({int(temp)}°C). Pack warm clothing and jackets."
        })
    
    if humidity > 75:
        recommendations.append({
            'type': 'info',
            'icon': '💧',
            'message': "High humidity expected. Extra water and cooling essentials recommended."
        })
    
    if wind_speed > 20:
        recommendations.append({
            'type': 'warning',
            'icon': '💨',
            'message': "Strong winds expected. Be cautious during outdoor activities."
        })
    
    return recommendations


@app.route('/analytics')
@login_required
def analytics():
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('dashboard'))
    query = db.session.query(
        BusService,
        func.coalesce(func.count(Booking.id), 0).label('booking_count'),
        Bus.bus_number,
        Bus.capacity
    )
    query = query.select_from(BusService)
    query = query.outerjoin(
        Booking,
        and_(
            Booking.service_number == BusService.service_number,
            Booking.status == 'confirmed',
            Booking.travel_date >= date.today()
        )
    )
    query = query.outerjoin(
        Bus,
        and_(
            Bus.bus_number == BusService.bus_number,
            Bus.service_id == BusService.id
        )
    )
    active_services = query.filter(
        BusService.is_active == True
    ).group_by(
        BusService.id,
        Bus.id,
        Bus.bus_number,
        Bus.capacity,
        BusService.service_number,
        BusService.origin,
        BusService.destination,
        BusService._via_routes
    ).all()
    route_data = {}
    for row in active_services:
        service = row[0]
        booking_count = row[1]
        bus_number = row[2]
        capacity = row[3]
        origin = service.origin.strip()
        destination = service.destination.strip()
        route_key = f"{origin} → {destination}"
        if route_key not in route_data:
            route_data[route_key] = {
                'passengers': 0,
                'service_count': 0,
                'buses': set(),
                'capacity': 0,
                'origin': origin,
                'destination': destination,
                'via_routes': set()
            }
        route_data[route_key]['passengers'] += booking_count
        route_data[route_key]['service_count'] += 1
        if bus_number:
            route_data[route_key]['buses'].add(bus_number)
        if capacity:
            route_data[route_key]['capacity'] += capacity
        if service.via_routes:
            for via in service.via_routes:
                if via.get('location'):
                    route_data[route_key]['via_routes'].add(via['location'])
    route_list = []
    for route_key, data in route_data.items():
        capacity = data.get('capacity', 1)
        occupancy_rate = round((data.get('passengers', 0) / max(1, capacity)) * 100, 1) if capacity > 0 else 0
        route_list.append({
            'name': route_key,
            'route': route_key,
            'passenger_count': data.get('passengers', 0),
            'bus_count': len(data.get('buses', [])),
            'service_count': data.get('service_count', 0),
            'capacity': capacity,
            'occupancy_rate': occupancy_rate,
            'origin': data.get('origin', ''),
            'destination': data.get('destination', ''),
            'via_routes': list(data.get('via_routes', [])),
            'buses': list(data.get('buses', []))
        })
    sorted_routes = sorted(
        route_list,
        key=lambda x: x['passenger_count'],
        reverse=True
    )
    route_labels = []
    passenger_counts = []
    service_counts = []
    for route_data in sorted_routes[:10]:
        route_labels.append(route_data['name'])
        passenger_counts.append(route_data['passenger_count'])
        service_counts.append(route_data['service_count'])
    current_year = date.today().year
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
             'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    monthly_bookings = db.session.query(
        func.strftime('%m', Booking.booking_time).label('month'),
        func.count(Booking.id).label('count')
    ).filter(
        func.strftime('%Y', Booking.booking_time) == str(current_year),
        Booking.status == 'confirmed'
    ).group_by('month').all()
    monthly_data = [0] * 12
    for month, count in monthly_bookings:
        month_index = int(month) - 1
        if 0 <= month_index < 12:
            monthly_data[month_index] = count
    
    # Add demo data if there's insufficient real data
    if sum(monthly_data) < 50:
        base_booking = random.randint(800, 1200)
        for i in range(12):
            variance = random.randint(-200, 250)
            monthly_data[i] = max(100, base_booking + variance)
    route_popularity_routes = []
    for route_data in sorted_routes:
        capacity = route_data.get('capacity', 1)
        passenger_count = route_data.get('passenger_count', 0)
        occupancy_rate = round((passenger_count / max(1, capacity)) * 100, 1) if capacity > 0 else 0
        route_name = route_data.get('name', route_data.get('route', ''))
        route_popularity_routes.append({
            'route': route_name,
            'name': route_name,
            'data': route_data,
            'passenger_count': passenger_count,
            'bus_count': route_data.get('bus_count', 0),
            'service_count': route_data.get('service_count', 0),
            'capacity': capacity,
            'occupancy_rate': occupancy_rate,
            'origin': route_data.get('origin', ''),
            'destination': route_data.get('destination', ''),
            'via_routes': route_data.get('via_routes', [])
        })
    # Add demo routes if there's insufficient real data
    if len(route_popularity_routes) < 5:
        demo_routes = [
            {'origin': 'Madurai', 'destination': 'Bengaluru', 'passengers': 1240},
            {'origin': 'Chennai', 'destination': 'Madurai', 'passengers': 980},
            {'origin': 'Coimbatore', 'destination': 'Bengaluru', 'passengers': 856},
            {'origin': 'Trichy', 'destination': 'Chennai', 'passengers': 720},
            {'origin': 'Madurai', 'destination': 'Trichy', 'passengers': 645}
        ]
        for i, demo_route in enumerate(demo_routes):
            if not any(r['origin'] == demo_route['origin'] and r['destination'] == demo_route['destination'] 
                      for r in route_popularity_routes):
                route_popularity_routes.append({
                    'route': f"{demo_route['origin']} → {demo_route['destination']}",
                    'passenger_count': demo_route['passengers'],
                    'bus_count': random.randint(3, 8),
                    'service_count': random.randint(2, 5),
                    'capacity': random.randint(180, 320),
                    'occupancy_rate': round((demo_route['passengers'] / random.randint(180, 320)) * 100, 1),
                    'origin': demo_route['origin'],
                    'destination': demo_route['destination'],
                    'via_routes': random.choice([['Tirupur', 'Salem'], ['Dindigul'], []]),
                    'name': f"{demo_route['origin']} → {demo_route['destination']}",
                    'data': {
                        'name': f"{demo_route['origin']} → {demo_route['destination']}"
                    }
                })
        # Re-sort routes by passenger count
        route_popularity_routes = sorted(route_popularity_routes, 
                                        key=lambda x: x['passenger_count'], reverse=True)
    
    route_popularity = {
        'routes': route_popularity_routes,
        'total_bookings': sum(p.get('passenger_count', 0) for p in route_popularity_routes)
    }
    peak_day_data = db.session.query(
        func.date(Booking.booking_time).label('day'),
        func.count(Booking.id).label('count')
    ).filter(
        Booking.status == 'confirmed',
        Booking.booking_time >= datetime.now() - timedelta(days=30)
    ).group_by('day').order_by(db.desc('count')).first()
    peak_hour_data = db.session.query(
        func.strftime('%H:00', Booking.booking_time).label('hour'),
        func.count(Booking.id).label('count')
    ).filter(
        Booking.status == 'confirmed',
        Booking.booking_time >= datetime.now() - timedelta(days=30)
    ).group_by('hour').order_by(db.desc('count')).first()
    total_bookings = db.session.query(func.count(Booking.id)).filter(
        Booking.status == 'confirmed',
        Booking.booking_time >= datetime.now() - timedelta(days=30)
    ).scalar() or 0
    
    # Travel Plans Analytics (for planning/recommendation system)
    total_travel_plans = TravelPlan.query.filter(
        TravelPlan.created_at >= datetime.now() - timedelta(days=30)
    ).count()
    
    # Popular search routes from travel plans
    popular_search_routes = db.session.query(
        TravelPlan.origin,
        TravelPlan.destination,
        func.count(TravelPlan.id).label('search_count')
    ).filter(
        TravelPlan.created_at >= datetime.now() - timedelta(days=30)
    ).group_by(TravelPlan.origin, TravelPlan.destination).order_by(db.desc('search_count')).limit(10).all()
    
    # Transport mode preferences
    mode_preferences = db.session.query(
        TravelPlan.transport_mode,
        func.count(TravelPlan.id).label('count')
    ).filter(
        TravelPlan.created_at >= datetime.now() - timedelta(days=30)
    ).group_by(TravelPlan.transport_mode).all()
    
    # Travel Guide usage
    travel_guide_routes = TravelRoute.query.filter(
        TravelRoute.created_at >= datetime.now() - timedelta(days=30)
    ).count()
    
    # Weather queries
    weather_queries = WeatherData.query.filter(
        WeatherData.updated_at >= datetime.now() - timedelta(days=30)
    ).count()
    
    # Crowd predictions generated
    crowd_predictions_count = CrowdPrediction.query.filter(
        CrowdPrediction.created_at >= datetime.now() - timedelta(days=30)
    ).count()
    
    avg_passengers = 1.0
    if total_bookings > 0:
        avg_passengers = round(total_bookings / 30, 1)
    
    # Generate demo peak hours data
    if not peak_day_data:
        demo_peak_day = datetime.now() - timedelta(days=random.randint(1, 15))
        peak_day_passengers = random.randint(85, 120)
    else:
        demo_peak_day = peak_day_data[0]
        peak_day_passengers = peak_day_data[1]
    
    if not peak_hour_data:
        demo_peak_hour = random.choice(['19:00', '20:00', '21:00', '22:00'])
        peak_hour_passengers = random.randint(45, 65)
    else:
        demo_peak_hour = peak_hour_data[0]
        peak_hour_passengers = peak_hour_data[1]
    
    if total_bookings == 0:
        total_bookings = random.randint(2400, 3200)
        avg_passengers = round(total_bookings / 30, 1)
    
    # Generate demo peak hours chart data
    weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    peak_hours_chart_data = {
        '6AM-10AM': [random.randint(45, 75) for _ in weekdays],
        '10AM-2PM': [random.randint(35, 60) for _ in weekdays],
        '2PM-6PM': [random.randint(40, 65) for _ in weekdays],
        '6PM-10PM': [random.randint(55, 85) for _ in weekdays]
    }
    
    peak_hours = {
        'peak_day': demo_peak_day.strftime('%A, %b %d') if hasattr(demo_peak_day, 'strftime') else 'No data',
        'peak_day_passengers': peak_day_passengers,
        'peak_hour': demo_peak_hour,
        'peak_hour_passengers': peak_hour_passengers,
        'total_bookings': total_bookings,
        'avg_passengers': avg_passengers,
        'weekdays': weekdays,
        'data': peak_hours_chart_data
    }
    booking_data = {
        'labels': months,
        'datasets': [{
            'label': 'Bookings',
            'data': monthly_data,
            'backgroundColor': 'rgba(78, 115, 223, 0.05)',
            'borderColor': 'rgba(78, 115, 223, 1)',
            'pointBackgroundColor': 'rgba(78, 115, 223, 1)',
            'pointBorderColor': '#fff',
            'pointHoverBackgroundColor': '#fff',
            'pointHoverBorderColor': 'rgba(78, 115, 223, 1)',
            'pointRadius': 3,
            'pointHoverRadius': 5,
            'borderWidth': 2,
            'fill': True
        }]
    }
    
    # Get booking status data for pie chart
    booking_status_query = db.session.query(
        Booking.status,
        func.count(Booking.id).label('count')
    ).filter(
        Booking.booking_time >= datetime.now() - timedelta(days=30)
    ).group_by(Booking.status).all()
    
    booking_status_data = {
        'confirmed': 0,
        'pending': 0,
        'cancelled': 0
    }
    
    for status, count in booking_status_query:
        status_lower = status.lower() if status else 'confirmed'
        if status_lower in booking_status_data:
            booking_status_data[status_lower] = count
    
    # If no data, use demo data
    if sum(booking_status_data.values()) == 0:
        booking_status_data = {
            'confirmed': random.randint(70, 85),
            'pending': random.randint(10, 20),
            'cancelled': random.randint(5, 10)
        }
    
    # Prepare route comparison bar chart data (top 10 routes)
    route_comparison_data = {
        'labels': route_labels[:10],
        'passenger_counts': passenger_counts[:10],
        'service_counts': service_counts[:10]
    }
    
    return render_template(
        'analytics/index.html',
        title='Analytics Dashboard',
        route_labels=route_labels,
        passenger_counts=passenger_counts,
        service_counts=service_counts,
        # New metrics for planning/recommendation system
        total_travel_plans=total_travel_plans,
        popular_search_routes=popular_search_routes,
        mode_preferences=mode_preferences,
        travel_guide_routes=travel_guide_routes,
        weather_queries=weather_queries,
        crowd_predictions_count=crowd_predictions_count,
        monthly_bookings=monthly_data,
        current_year=current_year,
        months=months,
        route_popularity=route_popularity,
        peak_hours=peak_hours,
        booking_data=booking_data,
        booking_status_data=booking_status_data,
        route_comparison_data=route_comparison_data
    )
@app.route('/admin/passengers')
@login_required
def manage_passengers():
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('dashboard'))
    passengers = []
    return render_template('passengers/manage.html',
                         passengers=passengers,
                         title='Manage Passengers')
@app.route('/admin/routes')
@login_required
def manage_routes():
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('dashboard'))
    routes = BusRoute.query.order_by(BusRoute.route_number).all()
    return render_template('routes/manage.html', routes=routes)
@app.route('/admin/routes/add', methods=['GET', 'POST'])
@login_required
def add_route():
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        try:
            route_data = {
                'route_number': request.form.get('route_number'),
                'name': request.form.get('name'),
                'start_point': request.form.get('start_point'),
                'end_point': request.form.get('end_point'),
                'stops': json.dumps([s.strip() for s in request.form.get('stops', '').split('\n') if s.strip()])
            }
            if not all(route_data.values()):
                flash('All fields are required', 'danger')
                return render_template('routes/add_edit.html', route=route_data)
            if BusRoute.query.filter_by(route_number=route_data['route_number']).first():
                flash('A route with this number already exists', 'danger')
                return render_template('routes/add_edit.html', route=route_data)
            new_route = BusRoute(**route_data)
            db.session.add(new_route)
            db.session.commit()
            flash('Route added successfully!', 'success')
            return redirect(url_for('manage_routes'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding route: {str(e)}', 'danger')
    return render_template('routes/add_edit.html', route=None)
@app.route('/admin/routes/edit/<int:route_id>', methods=['GET', 'POST'])
@login_required
def edit_route(route_id):
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('dashboard'))
    route = BusRoute.query.get_or_404(route_id)
    if request.method == 'POST':
        try:
            route.route_number = request.form.get('route_number')
            route.name = request.form.get('name')
            route.start_point = request.form.get('start_point')
            route.end_point = request.form.get('end_point')
            route.stops = json.dumps([s.strip() for s in request.form.get('stops', '').split('\n') if s.strip()])
            db.session.commit()
            flash('Route updated successfully!', 'success')
            return redirect(url_for('manage_routes'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating route: {str(e)}', 'danger')
    stops = json.loads(route.stops) if route.stops else []
    route.stops = '\n'.join(stops)
    return render_template('routes/add_edit.html', route=route)
@app.route('/admin/routes/delete/<int:route_id>', methods=['POST'])
@login_required
def delete_route(route_id):
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized access'}), 403
    try:
        route = BusRoute.query.get_or_404(route_id)
        if route.buses:
            return jsonify({
                'success': False,
                'message': 'Cannot delete route: There are buses assigned to this route.'
            }), 400
        db.session.delete(route)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Route deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
@app.route('/admin/generate_sample_data', methods=['POST'])
@login_required
def generate_sample_data():
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('dashboard'))
    try:
        db.drop_all()
        db.create_all()
        
        # Create admin user
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            admin = User(username='admin', is_admin=True)
            admin.set_password('admin123')
            db.session.add(admin)
        
        # Tamil Nadu cities
        cities = ['Madurai', 'Chennai', 'Bengaluru', 'Coimbatore', 'Trichy', 'Salem', 'Tirupur', 
                  'Erode', 'Dindigul', 'Sivakasi', 'Srivilliputhur', 'Rajapalayam', 'Tenkasi', 
                  'Tirumangalam', 'Hosur', 'Krishnankovil']
        
        # Generate Bus Services
        bus_services = [
            {
                'service_number': 'BS-001',
                'bus_number': 'TN-01-AB-1234',
                'origin': 'Madurai',
                'destination': 'Bengaluru',
                'departure': '20:00',
                'arrival': '06:00',
                'via': [{'name': 'Tirupur'}, {'name': 'Hosur'}]
            },
            {
                'service_number': 'BS-002',
                'bus_number': 'TN-02-CD-5678',
                'origin': 'Chennai',
                'destination': 'Madurai',
                'departure': '22:00',
                'arrival': '07:00',
                'via': [{'name': 'Trichy'}, {'name': 'Dindigul'}]
            },
            {
                'service_number': 'BS-003',
                'bus_number': 'TN-03-EF-9012',
                'origin': 'Coimbatore',
                'destination': 'Chennai',
                'departure': '21:00',
                'arrival': '05:00',
                'via': [{'name': 'Salem'}, {'name': 'Trichy'}]
            },
            {
                'service_number': 'BS-004',
                'bus_number': 'TN-04-GH-3456',
                'origin': 'Madurai',
                'destination': 'Trichy',
                'departure': '08:00',
                'arrival': '10:30',
                'via': [{'name': 'Tirumangalam'}]
            },
            {
                'service_number': 'BS-005',
                'bus_number': 'TN-05-IJ-7890',
                'origin': 'Bengaluru',
                'destination': 'Chennai',
                'departure': '19:00',
                'arrival': '06:30',
                'via': [{'name': 'Salem'}]
            },
            {
                'service_number': 'BS-006',
                'bus_number': 'TN-06-KL-2468',
                'origin': 'Trichy',
                'destination': 'Bengaluru',
                'departure': '20:30',
                'arrival': '07:00',
                'via': [{'name': 'Erode'}, {'name': 'Hosur'}]
            },
            {
                'service_number': 'BS-007',
                'bus_number': 'TN-07-MN-1357',
                'origin': 'Madurai',
                'destination': 'Coimbatore',
                'departure': '09:00',
                'arrival': '15:00',
                'via': [{'name': 'Dindigul'}, {'name': 'Tirupur'}]
            },
            {
                'service_number': 'BS-008',
                'bus_number': 'TN-08-OP-9753',
                'origin': 'Sivakasi',
                'destination': 'Chennai',
                'departure': '21:30',
                'arrival': '06:00',
                'via': [{'name': 'Madurai'}, {'name': 'Trichy'}]
            },
            {
                'service_number': 'BS-009',
                'bus_number': 'TN-09-QR-8642',
                'origin': 'Bengaluru',
                'destination': 'Madurai',
                'departure': '19:30',
                'arrival': '06:00',
                'via': [{'name': 'Hosur'}, {'name': 'Dindigul'}]
            },
            {
                'service_number': 'BS-010',
                'bus_number': 'TN-10-ST-7410',
                'origin': 'Chennai',
                'destination': 'Coimbatore',
                'departure': '23:00',
                'arrival': '08:00',
                'via': [{'name': 'Salem'}]
            }
        ]
        
        for service_data in bus_services:
            dep_time = datetime.strptime(service_data['departure'], '%H:%M').time()
            arr_time = datetime.strptime(service_data['arrival'], '%H:%M').time()
            
            # Create stops data based on origin, destination, and via routes
            stops = []
            if service_data['origin']:
                stops.append({'name': f"{service_data['origin']} Bus Stand", 'location': service_data['origin']})
            
            # Add via route stops
            for via in service_data.get('via', []):
                if isinstance(via, dict) and via.get('name'):
                    stops.append({'name': f"{via['name']} Stop", 'location': via['name']})
            
            if service_data['destination']:
                stops.append({'name': f"{service_data['destination']} Bus Stand", 'location': service_data['destination']})
            
            service = BusService(
                service_number=service_data['service_number'],
                bus_number=service_data['bus_number'],
                origin=service_data['origin'],
                destination=service_data['destination'],
                departure_time=dep_time,
                arrival_time=arr_time,
                origin_departure_time=dep_time,
                destination_arrival_time=arr_time,
                via_routes=service_data.get('via', []),
                stops=stops,
                route_description=f"Direct service from {service_data['origin']} to {service_data['destination']}",
                is_active=True
            )
            db.session.add(service)
            
            # Create corresponding bus
            bus = Bus(
                bus_number=service_data['bus_number'],
                license_plate=service_data['bus_number'].replace('-', ''),
                capacity=random.choice([40, 45, 50, 35]),
                current_passengers=random.randint(10, 35),
                status=random.choice(['active', 'active', 'active', 'maintenance']),
                current_location=f"{service_data['origin']} Bus Stand",
                service_id=None  # Will be updated after commit
            )
            db.session.add(bus)
        
        db.session.commit()
        
        # Update bus service_ids
        services = BusService.query.all()
        buses = Bus.query.all()
        for i, bus in enumerate(buses):
            if i < len(services):
                bus.service_id = services[i].id
        
        # Generate bookings for the last 12 months
        first_names = ['Ramesh', 'Suresh', 'Rajesh', 'Kumar', 'Lakshmi', 'Priya', 'Deepak', 'Arjun',
                      'Vijay', 'Karthik', 'Anjali', 'Mohan', 'Senthil', 'Selvi', 'Meena', 'Balaji']
        last_names = ['Kumar', 'Reddy', 'Naidu', 'Pillai', 'Iyer', 'Iyengar', 'Devar', 'Raj', 'Gandhi']
        
        # Create bookings spread over the year
        for month in range(1, 13):
            for _ in range(random.randint(200, 300)):  # 200-300 bookings per month
                # Random travel date in the past year
                travel_date = date.today() - timedelta(days=random.randint(0, 365))
                
                # Random service
                service = random.choice(services)
                passenger_name = f"{random.choice(first_names)} {random.choice(last_names)}"
                passenger_phone = f"98{random.randint(10000000, 99999999)}"
                
                booking = Booking(
                    passenger_name=passenger_name,
                    passenger_phone=passenger_phone,
                    service_number=service.service_number,
                    origin=service.origin,
                    destination=service.destination,
                    booking_time=travel_date - timedelta(days=random.randint(1, 30)),
                    travel_date=travel_date,
                    status=random.choice(['confirmed', 'confirmed', 'confirmed', 'pending', 'cancelled']),
                    seat_number=f"{random.randint(1, 50)}{random.choice(['A', 'B', 'C', 'D'])}",
                    fare=random.randint(500, 2500)
                )
                db.session.add(booking)
        
        # Create some logs
        for _ in range(500):
            log = Log(
                action_type=random.choice(['LOGIN', 'LOGOUT', 'CREATE', 'UPDATE', 'ACCESS', 'SYSTEM']),
                description=random.choice([
                    'User login successful',
                    'Bus service created',
                    'Booking confirmed',
                    'Route updated',
                    'Dashboard accessed',
                    'System backup completed',
                    'User logged out'
                ]),
                user_id=admin.id,
                endpoint=random.choice(['dashboard', 'login', 'analytics', 'bus-services']),
                method=random.choice(['GET', 'POST']),
                timestamp=datetime.now() - timedelta(days=random.randint(0, 90))
            )
            db.session.add(log)
        
        db.session.commit()
        flash('Comprehensive sample data generated successfully! Created buses, services, bookings, and logs.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error generating sample data: {str(e)}', 'danger')
    return redirect(url_for('dashboard'))
@app.route('/admin/crowd-management')
@login_required
def crowd_management():
    """Crowd Management - Special Events Page"""
    if not current_user.is_admin:
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('dashboard'))
    
    # Get upcoming and past events
    upcoming_events = SpecialEvent.query.filter(
        SpecialEvent.event_date >= date.today()
    ).order_by(SpecialEvent.event_date).all()
    
    past_events = SpecialEvent.query.filter(
        SpecialEvent.event_date < date.today()
    ).order_by(SpecialEvent.event_date.desc()).limit(10).all()
    
    # Get active events for today
    today_events = SpecialEvent.query.filter(
        SpecialEvent.event_date == date.today(),
        SpecialEvent.status == 'active'
    ).all()
    
    return render_template('crowd_management/index.html',
                         upcoming_events=upcoming_events,
                         past_events=past_events,
                         today_events=today_events)

@app.route('/admin/crowd-management/add', methods=['GET', 'POST'])
@login_required
def add_special_event():
    """Add a new special event"""
    if not current_user.is_admin:
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        try:
            event = SpecialEvent(
                event_name=request.form.get('event_name', '').strip(),
                event_description=request.form.get('event_description', '').strip(),
                location=request.form.get('location', '').strip(),
                event_date=datetime.strptime(request.form.get('event_date'), '%Y-%m-%d').date(),
                expected_demand=int(request.form.get('expected_demand', 100)),
                route_origin=request.form.get('route_origin', '').strip(),
                route_destination=request.form.get('route_destination', '').strip(),
                additional_buses=int(request.form.get('additional_buses', 0)),
                status=request.form.get('status', 'pending')
            )
            db.session.add(event)
            db.session.commit()
            
            log_action(
                action_type=LogActionType.CREATE,
                description=f"Created special event: {event.event_name}",
                user_id=current_user.id,
                ip_address=request.remote_addr,
                endpoint=request.endpoint
            )
            
            flash('Special event added successfully!', 'success')
            return redirect(url_for('crowd_management'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding special event: {str(e)}', 'danger')
    
    # Get all cities for dropdown
    cities = ['Krishnankovil', 'Madurai', 'Chennai', 'Bengaluru', 'Coimbatore', 'Trichy', 
              'Salem', 'Tirupur', 'Erode', 'Dindigul', 'Sivakasi', 'Srivilliputhur', 
              'Rajapalayam', 'Tenkasi', 'Tirumangalam', 'Hosur']
    
    return render_template('crowd_management/add_event.html', cities=cities)

@app.route('/admin/crowd-management/auto-suggest', methods=['POST'])
@login_required
def auto_suggest_buses():
    """Auto-suggest number of buses needed for an event"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    try:
        expected_demand = int(request.json.get('expected_demand', 100))
        bus_capacity = int(request.json.get('bus_capacity', 50))
        
        # Calculate how many buses are needed (account for 80% average capacity)
        buses_needed = max(1, int(expected_demand / (bus_capacity * 0.8)))
        
        return jsonify({
            'success': True,
            'buses_needed': buses_needed,
            'message': f'Suggested {buses_needed} buses for {expected_demand} passengers'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400

@app.route('/admin/crowd-management/<int:event_id>/update-status', methods=['POST'])
@login_required
def update_event_status(event_id):
    """Update event status"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    event = SpecialEvent.query.get_or_404(event_id)
    new_status = request.json.get('status')
    
    if new_status in ['pending', 'active', 'completed']:
        event.status = new_status
        db.session.commit()
        return jsonify({'success': True, 'message': 'Status updated successfully'})
    else:
        return jsonify({'success': False, 'message': 'Invalid status'}), 400

@app.route('/admin/crowd-management/<int:event_id>/delete', methods=['POST'])
@login_required
def delete_special_event(event_id):
    """Delete a special event"""
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('crowd_management'))
    
    event = SpecialEvent.query.get_or_404(event_id)
    
    try:
        db.session.delete(event)
        db.session.commit()
        flash('Special event deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting event: {str(e)}', 'danger')
    
    return redirect(url_for('crowd_management'))

@app.route('/admin/bus-services')
@login_required
def manage_bus_services():
    if not current_user.is_admin:
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('dashboard'))
    active_services = BusService.query.filter_by(is_active=True).order_by(BusService.created_at.desc()).all()
    inactive_services = BusService.query.filter_by(is_active=False).order_by(BusService.created_at.desc()).all()
    return render_template('manage_bus_services.html',
                         active_services=active_services,
                         inactive_services=inactive_services)
@app.route('/admin/bus-service/add', methods=['GET', 'POST'])
@login_required
def add_bus_service():
    """Add a new bus service with stops and via routes."""
    if not current_user.is_admin:
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        try:
            # Log the raw form data for debugging
            app.logger.info('Form data received: %s', dict(request.form))
            
            # Parse and validate basic service data
            service_data = {
                'service_number': request.form.get('service_number', '').strip(),
                'bus_number': request.form.get('bus_number', '').strip(),
                'origin': request.form.get('origin', '').strip(),
                'destination': request.form.get('destination', '').strip(),
                'route_description': request.form.get('route_description', '').strip(),
                'is_active': 'is_active' in request.form
            }
            
            # Parse time fields with validation
            time_fields = {
                'departure_time': request.form.get('departure_time'),
                'arrival_time': request.form.get('arrival_time'),
                'origin_departure_time': request.form.get('origin_departure_time'),
                'destination_arrival_time': request.form.get('destination_arrival_time')
            }
            
            try:
                for field, value in time_fields.items():
                    if value:
                        service_data[field] = datetime.strptime(value, '%H:%M').time()
            except ValueError as ve:
                app.logger.error(f'Error parsing time: {str(ve)}')
                flash('Invalid time format. Please use HH:MM format for all time fields.', 'danger')
                return render_template('bus_service_form.html', service=service_data)
            
            # Process via routes
            via_routes = []
            i = 0
            while f'via_routes[{i}][name]' in request.form:
                try:
                    name = request.form.get(f'via_routes[{i}][name]', '').strip()
                    time_str = request.form.get(f'via_routes[{i}][time]', '').strip()
                    
                    if name:  # Only add non-empty via routes
                        via_route = {'name': name}
                        if time_str:
                            try:
                                via_route['time'] = datetime.strptime(time_str, '%H:%M').time().strftime('%H:%M')
                            except ValueError:
                                via_route['time'] = time_str  # Keep as string if invalid format
                        via_routes.append(via_route)
                    i += 1
                except Exception as e:
                    app.logger.error(f'Error processing via route {i}: {str(e)}', exc_info=True)
                    i += 1  # Skip this route but continue with others
            
            # Process stops
            stops = []
            j = 0
            while f'stops[{j}][name]' in request.form:
                try:
                    name = request.form.get(f'stops[{j}][name]', '').strip()
                    arrival = request.form.get(f'stops[{j}][arrival]', '').strip()
                    departure = request.form.get(f'stops[{j}][departure]', '').strip()
                    
                    if name:  # Only add stops with a name
                        stop = {'name': name}
                        
                        # Process arrival time
                        if arrival:
                            try:
                                stop['arrival'] = datetime.strptime(arrival, '%H:%M').time().strftime('%H:%M')
                            except ValueError:
                                stop['arrival'] = arrival  # Keep as string if invalid format
                        
                        # Process departure time
                        if departure:
                            try:
                                stop['departure'] = datetime.strptime(departure, '%H:%M').time().strftime('%H:%M')
                            except ValueError:
                                stop['departure'] = departure  # Keep as string if invalid format
                        
                        stops.append(stop)
                    j += 1
                except Exception as e:
                    app.logger.error(f'Error processing stop {j}: {str(e)}', exc_info=True)
                    j += 1  # Skip this stop but continue with others
            
            # Log the processed data
            app.logger.info('Processed service data: %s', {
                'service': service_data,
                'via_routes_count': len(via_routes),
                'stops_count': len(stops)
            })
            
            # Create and save the service
            try:
                new_service = BusService(**service_data)
                new_service.via_routes = via_routes
                new_service.stops = stops
                
                db.session.add(new_service)
                db.session.commit()
                
                flash('Bus service added successfully!', 'success')
                log_action(LogActionType.CREATE, 
                         f'Added bus service {new_service.service_number}',
                         current_user.id, 
                         request.remote_addr,
                         request.endpoint,
                         request.method)
                
                return redirect(url_for('manage_bus_services'))
                
            except Exception as e:
                db.session.rollback()
                app.logger.error(f'Database error adding bus service: {str(e)}', exc_info=True)
                flash('A database error occurred while adding the bus service. Please check the logs for details.', 'danger')
                
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Unexpected error in add_bus_service: {str(e)}', exc_info=True)
            flash('An unexpected error occurred while adding the bus service. Please check the logs for details.', 'danger')
    
    # For GET requests or if there was an error
    return render_template('bus_service_form.html', service=None)
@app.route('/admin/bus-service/edit/<int:service_id>', methods=['GET', 'POST'])
@login_required
def edit_bus_service(service_id):
    if not current_user.is_admin:
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('dashboard'))
    service = BusService.query.get_or_404(service_id)
    if request.method == 'POST':
        try:
            service.service_number = request.form.get('service_number')
            service.bus_number = request.form.get('bus_number')
            service.origin = request.form.get('origin')
            service.destination = request.form.get('destination')
            service.departure_time = datetime.strptime(
                request.form.get('departure_time'),
                '%H:%M'
            ).time()
            service.arrival_time = datetime.strptime(
                request.form.get('arrival_time'),
                '%H:%M'
            ).time()
            service.route_description = request.form.get('route_description')
            service.is_active = 'is_active' in request.form
            service.via_routes = []
            i = 0
            while f'via_routes-{i}-name' in request.form:
                via_route = {
                    'name': request.form.get(f'via_routes-{i}-name'),
                    'time': datetime.strptime(
                        request.form.get(f'via_routes-{i}-time'),
                        '%H:%M'
                    ).time().strftime('%H:%M')
                }
                service.via_routes.append(via_route)
                i += 1
            service.stops = []
            j = 0
            while f'stops-{j}-name' in request.form:
                stop = {
                    'name': request.form.get(f'stops-{j}-name'),
                    'arrival': datetime.strptime(
                        request.form.get(f'stops-{j}-arrival'),
                        '%H:%M'
                    ).time().strftime('%H:%M'),
                    'departure': datetime.strptime(
                        request.form.get(f'stops-{j}-departure'),
                        '%H:%M'
                    ).time().strftime('%H:%M')
                }
                service.stops.append(stop)
                j += 1
            service.updated_at = datetime.utcnow()
            db.session.commit()
            flash('Bus service updated successfully!', 'success')
            return redirect(url_for('manage_bus_services'))
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Error updating bus service: {str(e)}')
            flash('An error occurred while updating the bus service. Please try again.', 'danger')
    return render_template('bus_service_form.html', service=service)
@app.route('/admin/bus-service/activate/<int:service_id>', methods=['POST'])
@login_required
def activate_bus_service(service_id):
    try:
        if not current_user.is_admin:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        service = BusService.query.get(service_id)
        if not service:
            return jsonify({'success': False, 'message': 'Service not found'}), 404
        service.is_active = True
        service.updated_at = datetime.utcnow()
        log_action(
            action_type=LogActionType.UPDATE,
            description=f"Activated bus service: {service.service_number} ({service.origin} to {service.destination})",
            user_id=current_user.id,
            ip_address=request.remote_addr,
            endpoint=request.endpoint,
            method=request.method,
            details={
                'service_id': service.id,
                'service_number': service.service_number,
                'origin': service.origin,
                'destination': service.destination,
                'departure_time': str(service.departure_time) if service.departure_time else None,
                'arrival_time': str(service.arrival_time) if service.arrival_time else None
            }
        )
        db.session.commit()
        service_data = {
            'id': service.id,
            'service_number': service.service_number,
            'bus_number': service.bus_number,
            'is_active': service.is_active,
            'updated_at': service.updated_at.strftime('%Y-%m-%d %H:%M:%S')
        }
        return jsonify({
            'success': True,
            'message': 'Service activated successfully',
            'service': service_data
        })
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error activating service {service_id}: {str(e)}")
        log_action(
            action_type=LogActionType.ERROR,
            description=f"Failed to activate bus service ID: {service_id}",
            user_id=current_user.id if current_user.is_authenticated else None,
            ip_address=request.remote_addr,
            endpoint=request.endpoint,
            method=request.method,
            details={
                'error': str(e),
                'service_id': service_id
            }
        )
        return jsonify({
            'success': False,
            'message': f'An error occurred: {str(e)}'
        }), 500
@app.route('/admin/bus-service/deactivate/<int:service_id>', methods=['POST'])
@login_required
def deactivate_bus_service(service_id):
    try:
        if not current_user.is_admin:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        service = BusService.query.get_or_404(service_id)
        service.is_active = False
        service.updated_at = datetime.utcnow()
        log_action(
            action_type=LogActionType.UPDATE,
            description=f"Deactivated bus service: {service.service_number} ({service.origin} to {service.destination})",
            user_id=current_user.id,
            ip_address=request.remote_addr,
            endpoint=request.endpoint,
            method=request.method,
            details={
                'service_id': service.id,
                'service_number': service.service_number,
                'origin': service.origin,
                'destination': service.destination,
                'departure_time': str(service.departure_time) if service.departure_time else None,
                'arrival_time': str(service.arrival_time) if service.arrival_time else None
            }
        )
        db.session.commit()
        return jsonify({
            'success': True,
            'message': 'Service deactivated successfully',
            'service': service.to_dict()
        })
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error deactivating service {service_id}: {str(e)}")
        log_action(
            action_type=LogActionType.ERROR,
            description=f"Failed to deactivate bus service ID: {service_id}",
            user_id=current_user.id if current_user.is_authenticated else None,
            ip_address=request.remote_addr,
            endpoint=request.endpoint,
            method=request.method,
            details={
                'error': str(e),
                'service_id': service_id
            }
        )
        return jsonify({
            'success': False,
            'message': f'An error occurred: {str(e)}'
        }), 500
# ==================== AI CROWD PREDICTION ENGINE ====================

def predict_crowd_at_stop(stop_name, target_date, target_hour, stop_location=None):
    """
    AI-driven crowd prediction engine using historical data and event intelligence
    Returns predicted passenger count and confidence level
    """
    base_prediction = 0
    confidence = 0.5
    
    # Get historical averages for this stop
    historical_data = StopAnalytics.query.filter(
        StopAnalytics.stop_name == stop_name,
        StopAnalytics.hour == target_hour
    ).all()
    
    if historical_data:
        avg_passengers = sum(d.passenger_count for d in historical_data) / len(historical_data)
        base_prediction = int(avg_passengers)
        confidence = min(0.8, 0.5 + (len(historical_data) / 100))
    
    # Apply weekday factor
    weekday = target_date.strftime('%A')
    weekday_factors = {
        'Monday': 1.0, 'Tuesday': 1.0, 'Wednesday': 1.0, 'Thursday': 1.0,
        'Friday': 1.2, 'Saturday': 1.3, 'Sunday': 1.4
    }
    base_prediction = int(base_prediction * weekday_factors.get(weekday, 1.0))
    
    # Apply time-of-day factor
    time_factors = {
        (6, 10): 1.3,   # Morning rush
        (10, 14): 0.8,  # Midday
        (14, 18): 1.1,  # Afternoon
        (18, 22): 1.5,  # Evening rush
        (22, 6): 0.5    # Late night
    }
    for (start, end), factor in time_factors.items():
        if start <= target_hour < end or (start > end and (target_hour >= start or target_hour < end)):
            base_prediction = int(base_prediction * factor)
            break
    
    # Check for events affecting this stop
    event_impact = 0
    events = SpecialEvent.query.filter(
        SpecialEvent.event_date == target_date,
        SpecialEvent.status.in_(['pending', 'active'])
    ).all()
    
    for event in events:
        if event.location == stop_location or stop_location in event.location:
            event_impact += event.expected_demand * 0.35
            confidence = min(0.95, confidence + 0.1)
    
    # Check impact zones
    stop = BusStop.query.filter_by(name=stop_name).first()
    if stop and stop.impact_zone:
        zone_multipliers = {
            'university': 1.4,  # University zones see 40% more traffic
            'airport': 1.6,      # Airport connections see 60% more
            'train_station': 1.5, # Train stations see 50% more
            'festival': 1.8,     # Festival areas see 80% more
            'hospital': 1.2      # Hospitals see 20% more
        }
        multiplier = zone_multipliers.get(stop.impact_zone, 1.0)
        base_prediction = int(base_prediction * multiplier)
    
    predicted_passengers = int(base_prediction + event_impact)
    
    # Generate recommendation
    recommendation = generate_recommendation(predicted_passengers, stop_name, target_hour)
    
    return {
        'predicted_passengers': predicted_passengers,
        'confidence': round(confidence, 2),
        'base_prediction': base_prediction,
        'event_impact': round(event_impact, 0),
        'factors': {
            'weekday': weekday,
            'hour': target_hour,
            'impact_zone': stop.impact_zone if stop else None,
            'events_count': len(events)
        },
        'recommendation': recommendation
    }

def generate_recommendation(predicted_passengers, stop_name, hour):
    """Generate AI recommendations based on crowd prediction"""
    if predicted_passengers > 100:
        return f"High crowd expected at {stop_name} ({hour}:00). Consider adding extra buses or rescheduling departures."
    elif predicted_passengers > 60:
        return f"Moderate crowd expected at {stop_name} ({hour}:00). Monitor closely and prepare for additional capacity if needed."
    elif predicted_passengers < 20:
        return f"Low passenger count expected at {stop_name} ({hour}:00). Normal operations should be sufficient."
    return "Normal passenger flow expected."

def recommend_optimal_time(passenger_phone, origin, destination, preferred_time):
    """
    Recommend optimal boarding time/stop to reduce waiting time
    """
    # Get predictions for next 6 hours
    recommendations = []
    current_hour = datetime.now().hour
    
    for hour_offset in range(6):
        check_hour = (current_hour + hour_offset) % 24
        check_date = date.today() + timedelta(days=1 if check_hour < current_hour else 0)
        
        prediction = predict_crowd_at_stop(origin, check_date, check_hour, origin)
        
        if prediction['predicted_passengers'] < 40:  # Less crowded
            recommendations.append({
                'time': f"{check_hour}:00",
                'passenger_count': prediction['predicted_passengers'],
                'reason': f"Lower crowd expected ({prediction['predicted_passengers']} passengers)"
            })
    
    if recommendations:
        best = min(recommendations, key=lambda x: x['passenger_count'])
        return {
            'recommended_time': best['time'],
            'expected_crowd': best['passenger_count'],
            'reason': best['reason']
        }
    return None

@app.route('/admin/ai-crowd-management')
@login_required
def ai_crowd_management():
    """AI-driven Crowd Management Dashboard"""
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('dashboard'))
    
    # Get all stops
    stops = BusStop.query.all()
    if not stops:
        # Initialize default stops for Srivilliputhur → Madurai route
        default_stops = [
            {'name': 'Srivilliputhur', 'location': 'Srivilliputhur', 'impact_zone': None, 'lat': 9.5121, 'lng': 77.6336},
            {'name': 'Krishnankovil', 'location': 'Krishnankovil', 'impact_zone': 'university', 'lat': 9.572774, 'lng': 77.678473},
            {'name': 'Alagapuri', 'location': 'Alagapuri', 'impact_zone': None, 'lat': 9.6845, 'lng': 77.8567},
            {'name': 'T. Kalupatti', 'location': 'T. Kalupatti', 'impact_zone': None, 'lat': 9.7123, 'lng': 77.9123},
            {'name': 'Tirumangalam', 'location': 'Tirumangalam', 'impact_zone': None, 'lat': 9.8234, 'lng': 78.0456},
            {'name': 'Madurai Ring Road', 'location': 'Madurai', 'impact_zone': None, 'lat': 9.9123, 'lng': 78.1234},
            {'name': 'Madurai Mattuthavani', 'location': 'Madurai', 'impact_zone': 'train_station', 'lat': 9.9256, 'lng': 78.1345}
        ]
        for stop_data in default_stops:
            stop = BusStop(
                name=stop_data['name'],
                location=stop_data['location'],
                latitude=stop_data.get('lat'),
                longitude=stop_data.get('lng'),
                impact_zone=stop_data.get('impact_zone')
            )
            db.session.add(stop)
        db.session.commit()
        stops = BusStop.query.all()
    
    # Get predictions for today and tomorrow
    today = date.today()
    tomorrow = today + timedelta(days=1)
    
    predictions_today = []
    predictions_tomorrow = []
    
    for stop in stops:
        for hour in [6, 9, 12, 15, 18, 21]:  # Key hours
            pred_today = predict_crowd_at_stop(stop.name, today, hour, stop.location)
            pred_tomorrow = predict_crowd_at_stop(stop.name, tomorrow, hour, stop.location)
            
            predictions_today.append({
                'stop': stop.name,
                'location': stop.location,
                'hour': hour,
                **pred_today
            })
            
            predictions_tomorrow.append({
                'stop': stop.name,
                'location': stop.location,
                'hour': hour,
                **pred_tomorrow
            })
    
    # Get active alerts
    active_alerts = AlertEvent.query.filter_by(status='active').order_by(AlertEvent.created_at.desc()).limit(10).all()
    
    # Get upcoming events
    upcoming_events = SpecialEvent.query.filter(
        SpecialEvent.event_date >= today,
        SpecialEvent.status.in_(['pending', 'active'])
    ).order_by(SpecialEvent.event_date).all()
    
    return render_template('ai_crowd_management/index.html',
                         stops=stops,
                         predictions_today=predictions_today,
                         predictions_tomorrow=predictions_tomorrow,
                         active_alerts=active_alerts,
                         upcoming_events=upcoming_events)

@app.route('/api/predict-crowd', methods=['POST'])
@login_required
def api_predict_crowd():
    """API endpoint for crowd prediction"""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.json
    stop_name = data.get('stop_name')
    target_date = datetime.strptime(data.get('date'), '%Y-%m-%d').date()
    target_hour = int(data.get('hour', 12))
    stop_location = data.get('location')
    
    prediction = predict_crowd_at_stop(stop_name, target_date, target_hour, stop_location)
    
    return jsonify(prediction)

@app.route('/api/recommend-passenger', methods=['POST'])
def api_recommend_passenger():
    """API endpoint for passenger recommendations"""
    data = request.json
    passenger_phone = data.get('phone')
    origin = data.get('origin')
    destination = data.get('destination')
    preferred_time = data.get('preferred_time', '12:00')
    
    recommendation = recommend_optimal_time(passenger_phone, origin, destination, preferred_time)
    
    if recommendation:
        # Save recommendation
        rec = PassengerRecommendation(
            passenger_phone=passenger_phone,
            origin=origin,
            destination=destination,
            recommended_time=datetime.strptime(recommendation['recommended_time'], '%H:%M').time(),
            reason=recommendation['reason']
        )
        db.session.add(rec)
        db.session.commit()
    
    return jsonify(recommendation if recommendation else {'message': 'No optimal time found'})

@app.route('/api/generate-alerts', methods=['POST'])
@login_required
def api_generate_alerts():
    """Generate alerts for predicted overcrowding"""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    today = date.today()
    alerts_generated = []
    
    stops = BusStop.query.all()
    for stop in stops:
        for hour in range(24):
            prediction = predict_crowd_at_stop(stop.name, today, hour, stop.location)
            
            if prediction['predicted_passengers'] > 80:  # Threshold for alert
                # Check if alert already exists
                existing = AlertEvent.query.filter_by(
                    stop_name=stop.name,
                    alert_type='overcrowding',
                    status='active'
                ).first()
                
                if not existing:
                    alert = AlertEvent(
                        alert_type='overcrowding',
                        stop_name=stop.name,
                        message=f"Expected {prediction['predicted_passengers']} passengers at {stop.name} at {hour}:00. Consider adding buses.",
                        severity='high' if prediction['predicted_passengers'] > 120 else 'medium',
                        predicted_time=datetime.combine(today, time(hour, 0)),
                        status='active'
                    )
                    db.session.add(alert)
                    alerts_generated.append(alert.to_dict())
    
    db.session.commit()
    return jsonify({'alerts_generated': len(alerts_generated), 'alerts': alerts_generated})

# ==================== WEATHER API INTEGRATION ====================
# Real-time data: OpenWeatherMap (OPENWEATHER_API_KEY) or Open-Meteo (no key, geocoded).
# In-memory cache (default 10 min) to limit API calls.

_WEATHER_LIVE_CACHE = {}

def _weather_cache_ttl():
    try:
        return max(60, min(3600, int(os.environ.get('WEATHER_CACHE_SECONDS', '600'))))
    except (TypeError, ValueError):
        return 600

def _weather_cache_get(key):
    t = _WEATHER_LIVE_CACHE.get(key)
    if not t:
        return None
    ts, data = t
    if time.time() - ts > _weather_cache_ttl():
        return None
    return data

def _weather_cache_set(key, data):
    _WEATHER_LIVE_CACHE[key] = (time.time(), data)


# Preferred lat/lon for OpenWeather when city name might be ambiguous
CITY_COORDINATES = {
    'Krishnankovil': (9.572774, 77.678473),
    'Sivakasi': (9.45, 77.82),
    'Srivilliputhur': (9.52, 77.63),
    'Tirumangalam': (9.82, 78.08),
    'Tenkasi': (8.95, 77.3),
    'Rajapalayam': (9.45, 77.57),
    'Madurai': (9.9252, 78.1198),
    'Dindigul': (10.35, 77.95),
    'Salem': (11.65, 78.17),
    'Erode': (11.34, 77.73),
    'Tiruppur': (11.11, 77.35),
    'Hosur': (12.74, 77.83),
    'Chennai': (13.0827, 80.2707),
    'Bengaluru': (12.9716, 77.5946),
    'Thanjavur': (10.7870, 79.1378),
    'Virudhunagar': (9.5819, 77.9624),
    'Palani': (10.4500, 77.5200),
    'Trichy': (10.7905, 78.7047),
    'Tiruchirappalli': (10.7905, 78.7047),
    'Coimbatore': (11.0168, 76.9558),
}


def _adv_key_from_api_condition(condition):
    c = (condition or 'clouds').lower()
    if c in ('rain', 'drizzle', 'thunderstorm'):
        return 'rainy'
    if c in ('mist', 'fog', 'haze', 'smoke', 'dust', 'sand', 'ash'):
        return 'foggy'
    if c == 'clear':
        return 'sunny'
    if c == 'clouds':
        return 'cloudy'
    if c == 'snow':
        return 'partly_cloudy'
    return 'partly_cloudy'


def _wmo_code_to_condition(wmo):
    """Map Open-Meteo WMO weather code to (api_condition, description)."""
    try:
        code = int(wmo)
    except (TypeError, ValueError):
        return 'clouds', 'Cloudy'
    table = {
        0: ('clear', 'Clear sky'),
        1: ('clouds', 'Mainly clear'),
        2: ('clouds', 'Partly cloudy'),
        3: ('clouds', 'Overcast'),
        45: ('mist', 'Fog'),
        48: ('mist', 'Depositing rime fog'),
        51: ('rain', 'Light drizzle'),
        53: ('rain', 'Drizzle'),
        55: ('rain', 'Dense drizzle'),
        56: ('rain', 'Freezing drizzle'),
        57: ('rain', 'Freezing drizzle'),
        61: ('rain', 'Slight rain'),
        63: ('rain', 'Moderate rain'),
        65: ('rain', 'Heavy rain'),
        66: ('rain', 'Freezing rain'),
        67: ('rain', 'Freezing rain'),
        71: ('snow', 'Slight snow'),
        73: ('snow', 'Moderate snow'),
        75: ('snow', 'Heavy snow'),
        77: ('snow', 'Snow grains'),
        80: ('rain', 'Rain showers'),
        81: ('rain', 'Heavy rain showers'),
        82: ('rain', 'Violent rain showers'),
        85: ('snow', 'Snow showers'),
        86: ('snow', 'Heavy snow showers'),
        95: ('thunderstorm', 'Thunderstorm'),
        96: ('thunderstorm', 'Thunderstorm with hail'),
        99: ('thunderstorm', 'Thunderstorm with heavy hail'),
    }
    return table.get(code, ('clouds', 'Cloudy'))


def _fetch_openweather_live(city_title):
    api_key = (os.environ.get('OPENWEATHER_API_KEY') or '').strip()
    if not api_key or api_key.lower() == 'demo' or not requests:
        return None
    city_key = city_title.strip()
    coords = CITY_COORDINATES.get(city_key) or CITY_COORDINATES.get(city_key.title())
    try:
        url = 'https://api.openweathermap.org/data/2.5/weather'
        params = {'appid': api_key, 'units': 'metric'}
        if coords:
            params['lat'] = coords[0]
            params['lon'] = coords[1]
        else:
            params['q'] = f'{city_key},IN'
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            app.logger.warning('OpenWeather HTTP %s for %s', r.status_code, city_title)
            return None
        data = r.json()
        w = (data.get('weather') or [{}])[0]
        main = data.get('main') or {}
        wind = data.get('wind') or {}
        return {
            'temperature': round(main.get('temp', 0)),
            'feels_like': round(main.get('feels_like', main.get('temp', 0))),
            'description': (w.get('description') or 'Weather').title(),
            'humidity': main.get('humidity'),
            'wind_speed': round(float(wind.get('speed') or 0), 1),
            'condition': (w.get('main') or 'Clouds').lower(),
            'icon': w.get('icon', '02d'),
            'source': 'openweathermap',
        }
    except Exception as e:
        app.logger.error('OpenWeather error %s: %s', city_title, e)
        return None


def _fetch_open_meteo_live(city_title):
    """Free current weather via Open-Meteo (no API key)."""
    if not requests:
        return None
    try:
        g = requests.get(
            'https://geocoding-api.open-meteo.com/v1/search',
            params={'name': city_title.strip(), 'count': 1, 'language': 'en', 'format': 'json'},
            timeout=8,
        )
        if g.status_code != 200:
            return None
        gj = g.json()
        results = gj.get('results') or []
        if not results:
            return None
        lat = results[0]['latitude']
        lon = results[0]['longitude']
        r = requests.get(
            'https://api.open-meteo.com/v1/forecast',
            params={
                'latitude': lat,
                'longitude': lon,
                'current': 'temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m',
                'timezone': 'auto',
            },
            timeout=12,
        )
        if r.status_code != 200:
            return None
        fj = r.json()
        cur = fj.get('current') or {}
        wmo = cur.get('weather_code', 3)
        cond, desc_text = _wmo_code_to_condition(wmo)
        return {
            'temperature': round(cur.get('temperature_2m', 0)),
            'feels_like': round(cur.get('apparent_temperature', cur.get('temperature_2m', 0))),
            'description': desc_text,
            'humidity': cur.get('relative_humidity_2m'),
            'wind_speed': round(float(cur.get('wind_speed_10m') or 0), 1),
            'condition': cond,
            'icon': '01d',
            'source': 'open-meteo',
        }
    except Exception as e:
        app.logger.error('Open-Meteo error %s: %s', city_title, e)
        return None


def _weather_demo_fallback(city_title):
    """Offline placeholder when APIs are unreachable."""
    demo_conditions = {
        'Krishnankovil': {'temp': 32, 'desc': 'Demo data (API offline)', 'condition': 'clouds'},
        'Chennai': {'temp': 35, 'desc': 'Demo data (API offline)', 'condition': 'clear'},
        'Bengaluru': {'temp': 28, 'desc': 'Demo data (API offline)', 'condition': 'clouds'},
        'Madurai': {'temp': 34, 'desc': 'Demo data (API offline)', 'condition': 'clear'},
    }
    key = city_title.strip().title()
    demo = demo_conditions.get(key, {'temp': 30, 'desc': 'Weather unavailable (demo)', 'condition': 'clouds'})
    return {
        'temperature': demo['temp'],
        'feels_like': demo['temp'] + random.randint(-2, 2),
        'description': demo['desc'],
        'humidity': random.randint(50, 80),
        'wind_speed': round(random.uniform(2, 12), 1),
        'condition': demo['condition'],
        'icon': '02d',
        'source': 'demo',
    }


def get_weather_data(location, target_date=None):
    """
    Current conditions from OpenWeatherMap (if OPENWEATHER_API_KEY) or Open-Meteo.
    Cached in memory ~10 minutes. Updates WeatherData for today.
    """
    if not location:
        return None
    loc = str(location).strip()
    if not loc:
        return None
    if target_date is None:
        target_date = date.today()

    if target_date != date.today():
        wrow = WeatherData.query.filter_by(location=loc, date=target_date).first()
        if wrow:
            d = wrow.to_dict()
            if not d.get('description') and d.get('forecast'):
                d['description'] = d['forecast']
            return d
        return None

    ck = loc.title()
    cached = _weather_cache_get(ck)
    if cached:
        return cached

    live = None
    if requests:
        live = _fetch_openweather_live(loc)
        if not live:
            live = _fetch_open_meteo_live(loc)
    if not live:
        live = _weather_demo_fallback(loc)

    cond = live.get('condition') or 'clouds'
    adv_key = _adv_key_from_api_condition(cond)
    advisory = generate_weather_advisory(adv_key, loc)
    t = live.get('temperature')
    desc = live.get('description') or ''
    forecast = f"{desc} · ~{t}°C" if t is not None else desc

    out = {
        'location': loc,
        'date': date.today().strftime('%Y-%m-%d'),
        'temperature': t,
        'feels_like': live.get('feels_like'),
        'condition': cond,
        'humidity': live.get('humidity'),
        'wind_speed': live.get('wind_speed'),
        'description': desc,
        'forecast': forecast,
        'advisory': advisory,
        'icon': live.get('icon'),
        'source': live.get('source', 'unknown'),
    }

    wrow = None
    try:
        wrow = WeatherData.query.filter_by(location=loc, date=date.today()).first()
        if wrow:
            wrow.condition = cond
            wrow.temperature = t
            wrow.humidity = live.get('humidity')
            wrow.wind_speed = live.get('wind_speed')
            wrow.forecast = forecast
            wrow.advisory = advisory
            wrow.updated_at = datetime.utcnow()
        else:
            wrow = WeatherData(
                location=loc,
                date=date.today(),
                condition=cond,
                temperature=t,
                humidity=live.get('humidity'),
                wind_speed=live.get('wind_speed'),
                forecast=forecast,
                advisory=advisory,
            )
            db.session.add(wrow)
        db.session.commit()
        out['id'] = wrow.id
    except Exception as e:
        db.session.rollback()
        app.logger.warning('Weather DB persist skipped: %s', e)

    _weather_cache_set(ck, out)
    return out


def generate_weather_advisory(condition, location):
    """Generate weather-aware travel advisory"""
    advisories = {
        'rainy': f"🌧️ Rain expected in {location}. Carry an umbrella or waterproof bag. Expect slight delays due to wet roads.",
        'foggy': f"🌫️ Fog alert on highway near {location} — expect slight delays. Drive carefully and allow extra travel time.",
        'sunny': f"☀️ Sunny day ahead in {location}. Stay hydrated and wear light clothes. Perfect weather for travel!",
        'cloudy': f"☁️ Cloudy conditions in {location}. Normal travel conditions expected.",
        'partly_cloudy': f"⛅ Partly cloudy in {location}. Good travel weather."
    }
    return advisories.get(condition, "Normal weather conditions expected.")

def calculate_duration_minutes(departure_time, arrival_time):
    """Calculate duration in minutes between two times"""
    if not departure_time or not arrival_time:
        return None
    
    try:
        if isinstance(departure_time, str):
            dep = datetime.strptime(departure_time, '%H:%M').time()
        else:
            dep = departure_time
        
        if isinstance(arrival_time, str):
            arr = datetime.strptime(arrival_time, '%H:%M').time()
        else:
            arr = arrival_time
        
        dep_dt = datetime.combine(date.today(), dep)
        arr_dt = datetime.combine(date.today(), arr)
        
        # Handle next day arrival
        if arr_dt < dep_dt:
            arr_dt += timedelta(days=1)
        
        duration = (arr_dt - dep_dt).total_seconds() / 60
        return int(duration)
    except:
        return None

def get_multi_modal_options(origin, destination, travel_date, preferred_time=None):
    """
    Get integrated travel options: Bus, Train, Flight with duration calculations
    Includes local buses and private operators
    """
    options = {
        'bus': [],
        'local_bus': [],
        'private_bus': [],
        'train': [],
        'flight': [],
        'multi_modal': [],
        'all_options': []  # Combined and sorted by duration
    }
    
    # Get regular bus services
    bus_services = BusService.query.filter(
        BusService.is_active == True,
        or_(
            and_(func.lower(BusService.origin) == func.lower(origin), 
                 func.lower(BusService.destination) == func.lower(destination)),
            and_(func.lower(BusService.origin) == func.lower(destination), 
                 func.lower(BusService.destination) == func.lower(origin))
        )
    ).all()
    
    for service in bus_services:
        # Get crowd prediction
        pred = predict_crowd_at_stop(origin, travel_date, preferred_time.hour if preferred_time else 12, origin)
        weather = get_weather_data(origin, travel_date)
        
        dep_time = service.origin_departure_time.strftime('%H:%M') if service.origin_departure_time else None
        arr_time = service.destination_arrival_time.strftime('%H:%M') if service.destination_arrival_time else None
        duration = calculate_duration_minutes(service.origin_departure_time, service.destination_arrival_time)
        
        bus_option = {
            'service_number': service.service_number,
            'origin': service.origin,
            'destination': service.destination,
            'departure_time': dep_time,
            'arrival_time': arr_time,
            'duration_minutes': duration,
            'duration_display': f"{duration // 60}h {duration % 60}m" if duration else "N/A",
            'via_routes': service.via_routes,
            'crowd_prediction': pred['predicted_passengers'],
            'weather_advisory': weather.get('advisory'),
            'weather_recommendations': get_actionable_weather_recommendations(weather),
            'recommendation': pred.get('recommendation'),
            'mode': 'bus',
            'type': 'regular'
        }
        options['bus'].append(bus_option)
        options['all_options'].append(bus_option)
    
    # Get local buses
    local_buses = LocalBus.query.filter(
        LocalBus.is_active == True,
        or_(
            and_(func.lower(LocalBus.origin) == func.lower(origin), 
                 func.lower(LocalBus.destination) == func.lower(destination)),
            and_(func.lower(LocalBus.origin) == func.lower(destination), 
                 func.lower(LocalBus.destination) == func.lower(origin))
        )
    ).all()
    
    for bus in local_buses:
        dep_time = bus.departure_time.strftime('%H:%M') if bus.departure_time else None
        arr_time = bus.arrival_time.strftime('%H:%M') if bus.arrival_time else None
        duration = calculate_duration_minutes(bus.departure_time, bus.arrival_time)
        weather = get_weather_data(origin, travel_date)
        
        local_option = {
            'service_number': bus.bus_number,
            'operator': bus.operator_name,
            'origin': bus.origin,
            'destination': bus.destination,
            'departure_time': dep_time,
            'arrival_time': arr_time,
            'duration_minutes': duration,
            'duration_display': f"{duration // 60}h {duration % 60}m" if duration else "N/A",
            'fare': bus.fare,
            'bus_type': bus.bus_type,
            'via_stops': bus.via_stops,
            'seat_availability': bus.seat_availability,
            'status': bus.status,
            'weather_recommendations': get_actionable_weather_recommendations(weather),
            'mode': 'bus',
            'type': 'local'
        }
        options['local_bus'].append(local_option)
        options['all_options'].append(local_option)
    
    # Get private buses
    private_buses = PrivateOperator.query.filter(
        PrivateOperator.is_active == True,
        or_(
            and_(func.lower(PrivateOperator.origin) == func.lower(origin), 
                 func.lower(PrivateOperator.destination) == func.lower(destination)),
            and_(func.lower(PrivateOperator.origin) == func.lower(destination), 
                 func.lower(PrivateOperator.destination) == func.lower(origin))
        )
    ).all()
    
    for bus in private_buses:
        dep_time = bus.departure_time.strftime('%H:%M') if bus.departure_time else None
        arr_time = bus.arrival_time.strftime('%H:%M') if bus.arrival_time else None
        duration = None
        if bus.duration:
            # Parse duration string like "9h 35m"
            try:
                parts = bus.duration.replace('h', ' ').replace('m', '').split()
                duration = int(parts[0]) * 60 + (int(parts[1]) if len(parts) > 1 else 0)
            except:
                duration = calculate_duration_minutes(bus.departure_time, bus.arrival_time)
        else:
            duration = calculate_duration_minutes(bus.departure_time, bus.arrival_time)
        
        weather = get_weather_data(origin, travel_date)
        
        private_option = {
            'service_number': bus.bus_number,
            'operator': bus.operator_name,
            'origin': bus.origin,
            'destination': bus.destination,
            'departure_time': dep_time,
            'arrival_time': arr_time,
            'duration_minutes': duration,
            'duration_display': bus.duration if bus.duration else (f"{duration // 60}h {duration % 60}m" if duration else "N/A"),
            'fare': bus.fare,
            'bus_type': bus.bus_type,
            'via_stops': bus.via_stops,
            'amenities': bus.amenities,
            'seat_availability': bus.seat_availability,
            'rating': bus.rating,
            'platform_source': bus.platform_source,
            'booking_url': bus.booking_url,
            'live_tracking': bus.live_tracking,
            'status': bus.status,
            'weather_recommendations': get_actionable_weather_recommendations(weather),
            'mode': 'bus',
            'type': 'private'
        }
        options['private_bus'].append(private_option)
        options['all_options'].append(private_option)
    
    # Get train schedules
    trains = TrainSchedule.query.filter(
        TrainSchedule.is_active == True,
        or_(
            and_(func.lower(TrainSchedule.origin_station) == func.lower(origin), 
                 func.lower(TrainSchedule.destination_station) == func.lower(destination)),
            and_(func.lower(TrainSchedule.origin_station) == func.lower(destination), 
                 func.lower(TrainSchedule.destination_station) == func.lower(origin))
        )
    ).all()
    
    for train in trains:
        dep_time = train.departure_time.strftime('%H:%M') if train.departure_time else None
        arr_time = train.arrival_time.strftime('%H:%M') if train.arrival_time else None
        duration = calculate_duration_minutes(train.departure_time, train.arrival_time)
        weather = get_weather_data(origin, travel_date)
        
        train_option = {
            'train_number': train.train_number,
            'train_name': train.train_name,
            'origin': train.origin_station,
            'destination': train.destination_station,
            'departure_time': dep_time,
            'arrival_time': arr_time,
            'duration_minutes': duration,
            'duration_display': f"{duration // 60}h {duration % 60}m" if duration else "N/A",
            'days_of_operation': train.days_of_operation,
            'weather_recommendations': get_actionable_weather_recommendations(weather),
            'mode': 'train',
            'type': 'train'
        }
        options['train'].append(train_option)
        options['all_options'].append(train_option)
    
    # Get flight schedules
    flights = FlightSchedule.query.filter(
        FlightSchedule.is_active == True,
        or_(
            and_(FlightSchedule.origin_airport.contains(origin), FlightSchedule.destination_airport.contains(destination)),
            and_(FlightSchedule.origin_airport.contains(destination), FlightSchedule.destination_airport.contains(origin))
        )
    ).all()
    
    for flight in flights:
        dep_time = flight.departure_time.strftime('%H:%M') if flight.departure_time else None
        arr_time = flight.arrival_time.strftime('%H:%M') if flight.arrival_time else None
        duration = calculate_duration_minutes(flight.departure_time, flight.arrival_time)
        weather = get_weather_data(origin, travel_date)
        
        flight_option = {
            'flight_number': flight.flight_number,
            'airline': flight.airline,
            'origin': flight.origin_airport,
            'destination': flight.destination_airport,
            'departure_time': dep_time,
            'arrival_time': arr_time,
            'duration_minutes': duration,
            'duration_display': f"{duration // 60}h {duration % 60}m" if duration else "N/A",
            'days_of_operation': flight.days_of_operation,
            'weather_recommendations': get_actionable_weather_recommendations(weather),
            'mode': 'flight',
            'type': 'flight'
        }
        options['flight'].append(flight_option)
        options['all_options'].append(flight_option)
    
    # Sort all options by duration (shortest first)
    options['all_options'].sort(key=lambda x: x.get('duration_minutes') or 9999)
    
    # Generate multi-modal options (bus + train, bus + flight)
    options['multi_modal'] = generate_multi_modal_connections(origin, destination, travel_date, options)
    
    return options

def get_actionable_weather_recommendations(weather_data):
    """Get actionable weather recommendations based on real-time weather"""
    if not weather_data:
        return []
    
    recommendations = []
    condition = weather_data.get('condition', '').lower()
    temp = weather_data.get('temperature', 25)
    humidity = weather_data.get('humidity', 50)
    
    if condition in ['rainy', 'rain', 'drizzle']:
        recommendations.append({
            'icon': '🌧️',
            'priority': 'high',
            'action': 'Carry umbrella or raincoat',
            'message': f"Rain expected. Carry an umbrella or waterproof raincoat. Keep your belongings in waterproof bags."
        })
        recommendations.append({
            'icon': '⏰',
            'priority': 'medium',
            'action': 'Allow extra travel time',
            'message': "Wet roads may cause delays. Plan to leave 15-20 minutes earlier."
        })
    elif condition == 'foggy':
        recommendations.append({
            'icon': '🌫️',
            'priority': 'high',
            'action': 'Expect delays',
            'message': "Fog alert! Expect 20-30 minutes delay. Drive carefully and allow extra time."
        })
    elif temp and temp > 35:
        recommendations.append({
            'icon': '🔥',
            'priority': 'medium',
            'action': 'Stay hydrated',
            'message': f"Very hot weather ({int(temp)}°C). Carry water bottle, wear light breathable clothes, and use sunscreen."
        })
    elif temp and temp < 15:
        recommendations.append({
            'icon': '🧥',
            'priority': 'medium',
            'action': 'Wear warm clothes',
            'message': f"Cool weather ({int(temp)}°C). Pack warm clothing, jacket, and keep yourself warm."
        })
    
    if humidity and humidity > 75:
        recommendations.append({
            'icon': '💧',
            'priority': 'low',
            'action': 'Extra water recommended',
            'message': "High humidity expected. Carry extra water and cooling essentials."
        })
    
    return recommendations

def generate_multi_modal_connections(origin, destination, travel_date, options):
    """Generate bus-train and bus-flight connection suggestions"""
    connections = []
    
    # Bus to Train connections
    for bus in options['bus']:
        for train in options['train']:
            if bus['destination'] == train['origin_station']:
                # Calculate if bus arrives in time for train
                bus_arrival = datetime.strptime(bus['arrival_time'], '%H:%M').time() if bus['arrival_time'] else None
                train_departure = datetime.strptime(train['departure_time'], '%H:%M').time()
                
                if bus_arrival:
                    time_diff = (datetime.combine(date.today(), train_departure) - 
                                datetime.combine(date.today(), bus_arrival)).total_seconds() / 60
                    
                    if 30 <= time_diff <= 120:  # 30 mins to 2 hours buffer
                        connections.append({
                            'type': 'bus_train',
                            'leg1': {
                                'mode': 'bus',
                                'service': bus['service_number'],
                                'from': bus['origin'],
                                'to': bus['destination'],
                                'departure': bus['departure_time'],
                                'arrival': bus['arrival_time']
                            },
                            'leg2': {
                                'mode': 'train',
                                'service': train['train_number'],
                                'from': train['origin_station'],
                                'to': train['destination_station'],
                                'departure': train['departure_time'],
                                'arrival': train['arrival_time']
                            },
                            'connection_time': f"{int(time_diff)} minutes",
                            'recommendation': f"Take bus from {bus['origin']} at {bus['departure_time']}, arrive at {bus['destination']} at {bus['arrival_time']}, then catch train {train['train_number']} at {train['departure_time']}"
                        })
    
    # Bus to Flight connections
    for bus in options['bus']:
        for flight in options['flight']:
            if bus['destination'] in flight['origin'] or flight['origin'] in bus['destination']:
                bus_arrival = datetime.strptime(bus['arrival_time'], '%H:%M').time() if bus['arrival_time'] else None
                flight_departure = datetime.strptime(flight['departure_time'], '%H:%M').time()
                
                if bus_arrival:
                    time_diff = (datetime.combine(date.today(), flight_departure) - 
                                datetime.combine(date.today(), bus_arrival)).total_seconds() / 60
                    
                    if 90 <= time_diff <= 180:  # 1.5 to 3 hours buffer for flights
                        connections.append({
                            'type': 'bus_flight',
                            'leg1': {
                                'mode': 'bus',
                                'service': bus['service_number'],
                                'from': bus['origin'],
                                'to': bus['destination'],
                                'departure': bus['departure_time'],
                                'arrival': bus['arrival_time']
                            },
                            'leg2': {
                                'mode': 'flight',
                                'service': flight['flight_number'],
                                'from': flight['origin'],
                                'to': flight['destination'],
                                'departure': flight['departure_time'],
                                'arrival': flight['arrival_time']
                            },
                            'connection_time': f"{int(time_diff)} minutes",
                            'recommendation': f"Take bus from {bus['origin']} at {bus['departure_time']}, arrive at {bus['destination']} at {bus['arrival_time']}, then catch flight {flight['flight_number']} at {flight['departure_time']}"
                        })
    
    return connections

def get_passenger_coordination_recommendations(origin, destination, travel_date, preferred_time=None):
    """
    Get multi-modal coordination recommendations for passengers
    Similar to admin dashboard but formatted for passenger view
    """
    recommendations = []
    today = travel_date if travel_date else date.today()
    
    # Get all available bus services
    bus_services = BusService.query.filter_by(is_active=True).all()
    
    # Get train schedules
    trains = TrainSchedule.query.filter_by(is_active=True).all()
    
    # Get flight schedules
    flights = FlightSchedule.query.filter_by(is_active=True).all()
    
    # Bus-Train Coordination
    for train in trains:
        if train.departure_time and train.origin_station:
            # Find buses that can connect to this train
            for bus in bus_services:
                if bus.destination_arrival_time and bus.destination:
                    # Check if bus destination matches train origin station
                    if (bus.destination.lower() in train.origin_station.lower() or 
                        train.origin_station.lower() in bus.destination.lower()):
                        
                        arrival_diff = (datetime.combine(today, train.departure_time) - 
                                       datetime.combine(today, bus.destination_arrival_time)).total_seconds() / 60
                        
                        # Good connection: 30 mins to 2 hours buffer
                        if 30 <= arrival_diff <= 120:
                            # Get crowd prediction
                            pred = predict_crowd_at_stop(bus.origin, today, 
                                                       bus.origin_departure_time.hour if bus.origin_departure_time else 12, 
                                                       bus.origin)
                            
                            recommendations.append({
                                'type': 'bus_train',
                                'icon': '🚌➡️🚂',
                                'title': f'Bus + Train Connection',
                                'description': f'Take bus {bus.service_number} from {bus.origin} to connect with train {train.train_number}',
                                'details': {
                                    'bus': {
                                        'service': bus.service_number,
                                        'from': bus.origin,
                                        'to': bus.destination,
                                        'departure': bus.origin_departure_time.strftime('%H:%M') if bus.origin_departure_time else 'N/A',
                                        'arrival': bus.destination_arrival_time.strftime('%H:%M') if bus.destination_arrival_time else 'N/A'
                                    },
                                    'train': {
                                        'number': train.train_number,
                                        'from': train.origin_station,
                                        'to': train.destination_station,
                                        'departure': train.departure_time.strftime('%H:%M') if train.departure_time else 'N/A',
                                        'arrival': train.arrival_time.strftime('%H:%M') if train.arrival_time else 'N/A'
                                    },
                                    'connection_time': f"{int(arrival_diff)} minutes",
                                    'crowd_prediction': pred.get('predicted_passengers', 0),
                                    'crowd_level': pred.get('crowd_level', 'moderate')
                                },
                                'recommendation': f"Board bus {bus.service_number} from {bus.origin} at {bus.origin_departure_time.strftime('%H:%M') if bus.origin_departure_time else 'N/A'}. Arrive at {bus.destination} at {bus.destination_arrival_time.strftime('%H:%M') if bus.destination_arrival_time else 'N/A'}, then catch train {train.train_number} at {train.departure_time.strftime('%H:%M') if train.departure_time else 'N/A'}. Connection time: {int(arrival_diff)} minutes."
                            })
    
    # Bus-Flight Coordination
    for flight in flights:
        if flight.departure_time and flight.origin_airport:
            for bus in bus_services:
                if bus.destination_arrival_time and bus.destination:
                    # Check if bus destination is near flight origin airport
                    airport_cities = {
                        'IXM': ['Madurai'],
                        'MAA': ['Chennai'],
                        'BLR': ['Bengaluru', 'Bangalore'],
                        'COK': ['Coimbatore']
                    }
                    
                    flight_origin_city = None
                    for code, cities in airport_cities.items():
                        if code in flight.origin_airport:
                            flight_origin_city = cities[0]
                            break
                    
                    if flight_origin_city and (flight_origin_city.lower() in bus.destination.lower() or 
                                               bus.destination.lower() in flight_origin_city.lower()):
                        
                        arrival_diff = (datetime.combine(today, flight.departure_time) - 
                                       datetime.combine(today, bus.destination_arrival_time)).total_seconds() / 60
                        
                        # Good connection: 1.5 to 3 hours buffer for flights
                        if 90 <= arrival_diff <= 180:
                            pred = predict_crowd_at_stop(bus.origin, today,
                                                         bus.origin_departure_time.hour if bus.origin_departure_time else 12,
                                                         bus.origin)
                            
                            recommendations.append({
                                'type': 'bus_flight',
                                'icon': '🚌➡️✈️',
                                'title': f'Bus + Flight Connection',
                                'description': f'Take bus {bus.service_number} from {bus.origin} to connect with flight {flight.flight_number}',
                                'details': {
                                    'bus': {
                                        'service': bus.service_number,
                                        'from': bus.origin,
                                        'to': bus.destination,
                                        'departure': bus.origin_departure_time.strftime('%H:%M') if bus.origin_departure_time else 'N/A',
                                        'arrival': bus.destination_arrival_time.strftime('%H:%M') if bus.destination_arrival_time else 'N/A'
                                    },
                                    'flight': {
                                        'number': flight.flight_number,
                                        'from': flight.origin_airport,
                                        'to': flight.destination_airport,
                                        'departure': flight.departure_time.strftime('%H:%M') if flight.departure_time else 'N/A',
                                        'arrival': flight.arrival_time.strftime('%H:%M') if flight.arrival_time else 'N/A'
                                    },
                                    'connection_time': f"{int(arrival_diff)} minutes",
                                    'crowd_prediction': pred.get('predicted_passengers', 0),
                                    'crowd_level': pred.get('crowd_level', 'moderate')
                                },
                                'recommendation': f"Board bus {bus.service_number} from {bus.origin} at {bus.origin_departure_time.strftime('%H:%M') if bus.origin_departure_time else 'N/A'}. Arrive at {bus.destination} at {bus.destination_arrival_time.strftime('%H:%M') if bus.destination_arrival_time else 'N/A'}, then catch flight {flight.flight_number} from {flight.origin_airport} at {flight.departure_time.strftime('%H:%M') if flight.departure_time else 'N/A'}. Connection time: {int(arrival_diff)} minutes. Allow extra time for airport check-in."
                            })
    
    # Filter recommendations based on origin/destination if provided
    if origin and destination:
        filtered = []
        for rec in recommendations:
            bus_from = rec['details']['bus']['from']
            final_to = rec['details'].get('train', {}).get('to') or rec['details'].get('flight', {}).get('to')
            
            # Include if it starts from origin or ends at destination
            if (origin.lower() in bus_from.lower() or 
                (final_to and destination.lower() in final_to.lower())):
                filtered.append(rec)
        recommendations = filtered
    
    # Sort by connection time (shorter is better)
    recommendations.sort(key=lambda x: int(x['details']['connection_time'].split()[0]) if x['details'].get('connection_time') else 999)
    
    return recommendations[:10]  # Return top 10 recommendations

@app.route('/travel-planner', methods=['GET', 'POST'])
def travel_planner():
    """Smart Travel Planner - Main entry point for passengers"""
    if request.method == 'POST':
        origin = request.form.get('origin', '').strip()
        destination = request.form.get('destination', '').strip()
        travel_date_str = request.form.get('travel_date')
        preferred_time_str = request.form.get('preferred_time')
        passenger_phone = request.form.get('phone', '').strip()
        passenger_name = request.form.get('name', '').strip()
        
        if not all([origin, destination, travel_date_str]):
            flash('Please fill in all required fields', 'danger')
            return redirect(url_for('travel_planner'))
        
        travel_date = datetime.strptime(travel_date_str, '%Y-%m-%d').date()
        preferred_time = datetime.strptime(preferred_time_str, '%H:%M').time() if preferred_time_str else None
        
        # Get multi-modal options
        options = get_multi_modal_options(origin, destination, travel_date, preferred_time)
        
        # Get weather for origin, destination, and key stops
        weather_origin = get_weather_data(origin, travel_date)
        weather_destination = get_weather_data(destination, travel_date)
        
        # Get crowd predictions
        crowd_predictions = {}
        if preferred_time:
            pred = predict_crowd_at_stop(origin, travel_date, preferred_time.hour, origin)
            crowd_predictions['origin'] = pred
        
        # Get personalized recommendations
        personalized_recs = []
        if passenger_phone:
            pref = PassengerPreference.query.filter_by(passenger_phone=passenger_phone).first()
            if pref and pref.preferred_times:
                personalized_recs = get_personalized_recommendations(passenger_phone, origin, destination, travel_date)
        
        # Save travel plan
        plan_details = {
            'options': options,
            'weather': {
                'origin': weather_origin,
                'destination': weather_destination
            },
            'crowd_predictions': crowd_predictions
        }
        
        travel_plan = TravelPlan(
            passenger_phone=passenger_phone if passenger_phone else None,  # Optional - only if provided for notifications
            passenger_name=passenger_name if passenger_name else None,  # Optional
            origin=origin,
            destination=destination,
            travel_date=travel_date,
            preferred_time=preferred_time,
            transport_mode='multi_modal' if options['multi_modal'] else 'bus',
            plan_details=plan_details,
            weather_alert=f"{weather_origin.get('advisory', '')} {weather_destination.get('advisory', '')}",
            crowd_prediction=crowd_predictions.get('origin', {}).get('predicted_passengers', 0)
        )
        db.session.add(travel_plan)
        db.session.commit()
        
        # Generate notifications if needed
        generate_travel_notifications(travel_plan.id, passenger_phone, options, weather_origin, weather_destination)
        
        # Get multi-modal coordination recommendations (bus-train, bus-flight connections)
        coordination_recommendations = get_passenger_coordination_recommendations(origin, destination, travel_date, preferred_time)
        
        return render_template('travel_planner/results.html',
                             travel_plan=travel_plan,
                             options=options,
                             weather_origin=weather_origin,
                             weather_destination=weather_destination,
                             crowd_predictions=crowd_predictions,
                             personalized_recs=personalized_recs,
                             coordination_recommendations=coordination_recommendations)
    
    # Get all cities for dropdown
    cities = ['Srivilliputhur', 'Krishnankovil', 'Alagapuri', 'T. Kalupatti', 'Tirumangalam', 
              'Madurai', 'Madurai Mattuthavani', 'Chennai', 'Bengaluru', 'Coimbatore', 
              'Trichy', 'Salem', 'Tirupur', 'Erode', 'Dindigul']
    
    return render_template('travel_planner/index.html', cities=cities)

def get_personalized_recommendations(passenger_phone, origin, destination, travel_date):
    """Get personalized recommendations based on travel history"""
    pref = PassengerPreference.query.filter_by(passenger_phone=passenger_phone).first()
    if not pref:
        return []
    
    recommendations = []
    
    # Check preferred times
    if pref.preferred_times:
        for hour in pref.preferred_times:
            pred = predict_crowd_at_stop(origin, travel_date, hour, origin)
            if pred['predicted_passengers'] < 50:
                recommendations.append({
                    'type': 'time',
                    'message': f"Based on your preferences, {hour}:00 is a good time with low crowd ({pred['predicted_passengers']} passengers expected)"
                })
    
    # Check preferred stops
    if pref.preferred_stops and origin not in pref.preferred_stops:
        nearby_stops = get_nearby_stops(origin)
        for stop in nearby_stops:
            if stop in pref.preferred_stops:
                recommendations.append({
                    'type': 'stop',
                    'message': f"You prefer {stop} stop. Consider boarding there instead."
                })
    
    return recommendations

def get_nearby_stops(stop_name):
    """Get nearby stops for alternate boarding"""
    # This would use geo-coordinates in production
    nearby_map = {
        'Krishnankovil': ['Alagapuri', 'T. Kalupatti'],
        'Madurai': ['Tirumangalam', 'Madurai Ring Road'],
        'Srivilliputhur': ['Krishnankovil']
    }
    return nearby_map.get(stop_name, [])

def generate_travel_notifications(plan_id, passenger_phone, options, weather_origin, weather_destination):
    """Generate intelligent notifications for travel plan"""
    if not passenger_phone:
        return
    
    notifications = []
    
    # Weather notifications
    if weather_origin.get('condition') in ['rainy', 'foggy']:
        notifications.append(TravelNotification(
            passenger_phone=passenger_phone,
            notification_type='weather',
            title='Weather Alert',
            message=weather_origin.get('advisory', ''),
            related_plan_id=plan_id,
            priority='high'
        ))
    
    # Crowd notifications
    for bus in options.get('bus', []):
        if bus.get('crowd_prediction', 0) > 80:
            notifications.append(TravelNotification(
                passenger_phone=passenger_phone,
                notification_type='crowd',
                title='High Crowd Expected',
                message=f"High crowd expected at {bus['origin']} ({bus.get('crowd_prediction')} passengers). Consider alternative times.",
                related_plan_id=plan_id,
                priority='normal'
            ))
    
    # Connection notifications
    for connection in options.get('multi_modal', []):
        notifications.append(TravelNotification(
            passenger_phone=passenger_phone,
            notification_type='connection',
            title='Multi-Modal Connection Available',
            message=connection.get('recommendation', ''),
            related_plan_id=plan_id,
            priority='normal'
        ))
    
    for notification in notifications:
        db.session.add(notification)
    db.session.commit()

@app.route('/passenger/dashboard')
def passenger_dashboard():
    """Passenger Dashboard with personalized view"""
    passenger_phone = request.args.get('phone', '')
    
    if not passenger_phone:
        return render_template('travel_planner/dashboard.html', 
                             plans=[],
                             notifications=[],
                             preferences=None)
    
    # Get active travel plans
    plans = TravelPlan.query.filter_by(
        passenger_phone=passenger_phone,
        status='active'
    ).order_by(TravelPlan.travel_date).all()
    
    # Get notifications
    notifications = TravelNotification.query.filter_by(
        passenger_phone=passenger_phone,
        status='unread'
    ).order_by(TravelNotification.created_at.desc()).limit(10).all()
    
    # Get preferences
    preferences = PassengerPreference.query.filter_by(passenger_phone=passenger_phone).first()
    
    return render_template('travel_planner/dashboard.html',
                         plans=plans,
                         notifications=notifications,
                         preferences=preferences,
                         passenger_phone=passenger_phone)

@app.route('/admin/multi-modal-coordination')
@login_required
def multi_modal_coordination():
    """Operator Dashboard for Multi-Modal Coordination"""
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('dashboard'))
    
    # Auto-update database schema if needed
    try:
        from sqlalchemy import text, inspect
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('private_operator')]
        
        if 'live_tracking' not in columns:
            try:
                db.session.execute(text("ALTER TABLE private_operator ADD COLUMN live_tracking BOOLEAN DEFAULT 1"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                app.logger.warning(f"Schema update warning: {e}")
        
        if 'duration' not in columns:
            try:
                db.session.execute(text("ALTER TABLE private_operator ADD COLUMN duration VARCHAR(20)"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                app.logger.warning(f"Schema update warning: {e}")
    except Exception as e:
        app.logger.error(f"Schema check error: {e}")
    
    today = date.today()
    
    # Update real-time status
    update_realtime_status()
    
    # Get train schedules
    trains = TrainSchedule.query.filter_by(is_active=True).all()
    
    # Get flight schedules
    flights = FlightSchedule.query.filter_by(is_active=True).all()
    
    # Get all bus services
    bus_services = BusService.query.filter_by(is_active=True).all()
    local_buses = LocalBus.query.filter_by(is_active=True).order_by(LocalBus.departure_time).limit(20).all()
    
    # Try to get private buses, with error handling
    try:
        private_buses = PrivateOperator.query.filter_by(is_active=True).order_by(PrivateOperator.departure_time).limit(20).all()
    except Exception as e:
        app.logger.error(f"Error querying private buses: {e}")
        # Try to update schema again
        try:
            from sqlalchemy import text
            db.session.execute(text("ALTER TABLE private_operator ADD COLUMN live_tracking BOOLEAN DEFAULT 1"))
            db.session.execute(text("ALTER TABLE private_operator ADD COLUMN duration VARCHAR(20)"))
            db.session.commit()
            private_buses = PrivateOperator.query.filter_by(is_active=True).order_by(PrivateOperator.departure_time).limit(20).all()
        except Exception as schema_error:
            app.logger.error(f"Schema update failed: {schema_error}")
            private_buses = []
            flash('Database schema update needed. Please click "Update DB Schema" button.', 'warning')
    
    # Get real-time status for buses
    realtime_statuses = {}
    for bus in local_buses[:10]:  # Limit to 10 for performance
        status = RealTimeBusStatus.query.filter_by(bus_id=bus.id, bus_type='local').first()
        if status:
            realtime_statuses[f"local_{bus.id}"] = status.to_dict()
    
    for bus in private_buses[:10]:
        status = RealTimeBusStatus.query.filter_by(bus_id=bus.id, bus_type='private').first()
        if status:
            realtime_statuses[f"private_{bus.id}"] = status.to_dict()
    
    # Generate coordination recommendations
    coordination_recommendations = []
    
    # Bus-Train Coordination
    for train in trains:
        if train.departure_time:
            # Find buses that can connect to this train
            target_arrival = (datetime.combine(date.today(), train.departure_time) - timedelta(minutes=45)).time()
            
            for bus in bus_services:
                if bus.destination_arrival_time and bus.destination == train.origin_station:
                    arrival_diff = (datetime.combine(date.today(), train.departure_time) - 
                                   datetime.combine(date.today(), bus.destination_arrival_time)).total_seconds() / 60
                    
                    if 30 <= arrival_diff <= 120:
                        # Get crowd prediction
                        pred = predict_crowd_at_stop(bus.origin, today, bus.origin_departure_time.hour if bus.origin_departure_time else 12, bus.origin)
                        
                        coordination_recommendations.append({
                            'type': 'bus_train',
                            'train': train.train_number,
                            'train_departure': train.departure_time.strftime('%H:%M'),
                            'bus': bus.service_number,
                            'bus_arrival': bus.destination_arrival_time.strftime('%H:%M'),
                            'connection_time': f"{int(arrival_diff)} min",
                            'crowd_prediction': pred['predicted_passengers'],
                            'recommendation': f"Bus {bus.service_number} from {bus.origin} connects well to train {train.train_number} at {train.origin_station}. Expected crowd: {pred['predicted_passengers']} passengers."
                        })
    
    # Bus-Flight Coordination
    for flight in flights:
        if flight.departure_time:
            target_arrival = (datetime.combine(date.today(), flight.departure_time) - timedelta(hours=2)).time()
            
            for bus in bus_services:
                if bus.destination_arrival_time and (flight.origin_airport in bus.destination or bus.destination in flight.origin_airport):
                    arrival_diff = (datetime.combine(date.today(), flight.departure_time) - 
                                   datetime.combine(date.today(), bus.destination_arrival_time)).total_seconds() / 60
                    
                    if 90 <= arrival_diff <= 180:
                        pred = predict_crowd_at_stop(bus.origin, today, bus.origin_departure_time.hour if bus.origin_departure_time else 12, bus.origin)
                        
                        coordination_recommendations.append({
                            'type': 'bus_flight',
                            'flight': flight.flight_number,
                            'flight_departure': flight.departure_time.strftime('%H:%M'),
                            'bus': bus.service_number,
                            'bus_arrival': bus.destination_arrival_time.strftime('%H:%M'),
                            'connection_time': f"{int(arrival_diff)} min",
                            'crowd_prediction': pred['predicted_passengers'],
                            'recommendation': f"Bus {bus.service_number} from {bus.origin} connects to flight {flight.flight_number}. Expected crowd: {pred['predicted_passengers']} passengers."
                        })
    
    # Focus cities
    focus_cities = ['Srivilliputhur', 'Krishnankovil', 'Madurai', 'Chennai', 
                    'Bengaluru', 'Coimbatore', 'Dindugal', 'Sivakasi']
    
    # Get buses for focus cities
    focus_local_buses = LocalBus.query.filter(
        LocalBus.is_active == True,
        or_(
            LocalBus.origin.in_(focus_cities),
            LocalBus.destination.in_(focus_cities)
        )
    ).order_by(LocalBus.departure_time).all()
    
    focus_private_buses = PrivateOperator.query.filter(
        PrivateOperator.is_active == True,
        or_(
            PrivateOperator.origin.in_(focus_cities),
            PrivateOperator.destination.in_(focus_cities)
        )
    ).order_by(PrivateOperator.departure_time).all()
    
    return render_template('admin/multi_modal_coordination.html',
                         trains=trains,
                         flights=flights,
                         bus_services=bus_services,
                         local_buses=local_buses,
                         private_buses=private_buses,
                         focus_local_buses=focus_local_buses,
                         focus_private_buses=focus_private_buses,
                         coordination_recommendations=coordination_recommendations,
                         realtime_statuses=realtime_statuses,
                         focus_cities=focus_cities)

@app.route('/admin/init-transport-schedules', methods=['POST'])
@login_required
def init_transport_schedules():
    """Initialize sample train and flight schedules"""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Sample train schedules
    train_schedules = [
        {'train_number': '12635', 'train_name': 'Madurai - Chennai Express', 
         'origin_station': 'Madurai', 'destination_station': 'Chennai', 
         'departure_time': '19:00', 'arrival_time': '06:30', 'days_of_operation': 'Daily'},
        {'train_number': '12636', 'train_name': 'Chennai - Madurai Express',
         'origin_station': 'Chennai', 'destination_station': 'Madurai',
         'departure_time': '21:00', 'arrival_time': '08:30', 'days_of_operation': 'Daily'},
        {'train_number': '12675', 'train_name': 'Madurai - Bengaluru Express',
         'origin_station': 'Madurai', 'destination_station': 'Bengaluru',
         'departure_time': '18:00', 'arrival_time': '07:00', 'days_of_operation': 'Daily'}
    ]
    
    for train_data in train_schedules:
        existing = TrainSchedule.query.filter_by(train_number=train_data['train_number']).first()
        if not existing:
            train = TrainSchedule(
                train_number=train_data['train_number'],
                train_name=train_data['train_name'],
                origin_station=train_data['origin_station'],
                destination_station=train_data['destination_station'],
                departure_time=datetime.strptime(train_data['departure_time'], '%H:%M').time(),
                arrival_time=datetime.strptime(train_data['arrival_time'], '%H:%M').time(),
                days_of_operation=train_data['days_of_operation']
            )
            db.session.add(train)
    
    # Sample flight schedules
    flight_schedules = [
        {'flight_number': '6E-1234', 'airline': 'IndiGo',
         'origin_airport': 'Madurai Airport', 'destination_airport': 'Chennai Airport',
         'departure_time': '20:00', 'arrival_time': '21:30', 'days_of_operation': 'Daily'},
        {'flight_number': '6E-5678', 'airline': 'IndiGo',
         'origin_airport': 'Chennai Airport', 'destination_airport': 'Madurai Airport',
         'departure_time': '22:00', 'arrival_time': '23:30', 'days_of_operation': 'Daily'},
        {'flight_number': 'SG-9012', 'airline': 'SpiceJet',
         'origin_airport': 'Madurai Airport', 'destination_airport': 'Bengaluru Airport',
         'departure_time': '19:30', 'arrival_time': '21:00', 'days_of_operation': 'Daily'}
    ]
    
    for flight_data in flight_schedules:
        existing = FlightSchedule.query.filter_by(flight_number=flight_data['flight_number']).first()
        if not existing:
            flight = FlightSchedule(
                flight_number=flight_data['flight_number'],
                airline=flight_data['airline'],
                origin_airport=flight_data['origin_airport'],
                destination_airport=flight_data['destination_airport'],
                departure_time=datetime.strptime(flight_data['departure_time'], '%H:%M').time(),
                arrival_time=datetime.strptime(flight_data['arrival_time'], '%H:%M').time(),
                days_of_operation=flight_data['days_of_operation']
            )
            db.session.add(flight)
    
    db.session.commit()
    flash('Transport schedules initialized successfully!', 'success')
    return redirect(url_for('multi_modal_coordination'))

# ==================== REDBUS-LIKE PLATFORM INTEGRATION ====================

def get_real_bus_data(origin, destination):
    """Get real bus data from RedBus/AbhiBus/MakeMyTrip platforms"""
    # Real data from RedBus, AbhiBus, MakeMyTrip
    real_data = {
        ('Srivilliputhur', 'Chennai'): [
            {
                'operator': 'TVLS Travels',
                'departure_time': '19:45',
                'arrival_time': '06:00',
                'fare': 500,
                'bus_type': 'Sleeper/AC (varies)',
                'seats_left': None,
                'live_tracking': True,
                'source': 'redBus'
            },
            {
                'operator': 'PALSLNT Travels',
                'departure_time': '20:20',
                'arrival_time': '05:25',
                'fare': 650,
                'bus_type': 'Non A/C Seater/Sleeper (2+1)',
                'seats_left': 37,
                'live_tracking': True,
                'source': 'MakeMyTrip'
            },
            {
                'operator': 'National Travels CHN',
                'departure_time': '20:10',
                'arrival_time': '06:30',
                'fare': 500,
                'bus_type': 'Non A/C Seater/Sleeper (2+1)',
                'seats_left': 28,
                'live_tracking': True,
                'source': 'MakeMyTrip / AbhiBus'
            },
            {
                'operator': 'SBM TRANSPORT',
                'departure_time': '21:00',
                'arrival_time': '06:45',
                'fare': 1000,
                'bus_type': 'A/C Sleeper (2+1)',
                'seats_left': 26,
                'live_tracking': True,
                'source': 'MakeMyTrip'
            },
            {
                'operator': 'LION Travels',
                'departure_time': '20:30',
                'arrival_time': '07:20',
                'fare': 800,
                'bus_type': 'A/C Seater / Sleeper (3+1 / 2+1)',
                'seats_left': 23,
                'live_tracking': True,
                'source': 'MakeMyTrip'
            }
        ],
        ('Srivilliputhur', 'Bengaluru'): [
            {
                'operator': 'Royal Roadlinks',
                'departure_time': '21:25',
                'arrival_time': '07:00',
                'fare': 949,
                'bus_type': 'A/C Sleeper (2+1)',
                'seats_left': None,
                'live_tracking': True,
                'source': 'AbhiBus',
                'duration': '9h 35m'
            },
            {
                'operator': 'Royal Travels',
                'departure_time': '20:10',
                'arrival_time': '06:00',
                'fare': 900,
                'bus_type': 'Non-A/C Sleeper (2+1)',
                'seats_left': None,
                'live_tracking': True,
                'source': 'AbhiBus / Paytm',
                'duration': '9h 50m'
            },
            {
                'operator': 'PSS Transport',
                'departure_time': '21:55',
                'arrival_time': '07:00',
                'fare': 949,
                'bus_type': 'A/C Sleeper (2+1)',
                'seats_left': None,
                'live_tracking': True,
                'source': 'AbhiBus',
                'duration': '9h 05m'
            },
            {
                'operator': 'A1 Travels',
                'departure_time': '20:45',
                'arrival_time': '06:00',
                'fare': 750,
                'bus_type': 'Non-A/C Seater/Sleeper (2+1)',
                'seats_left': None,
                'live_tracking': True,
                'source': 'MakeMyTrip',
                'duration': '9h 15m'
            },
            {
                'operator': 'Janaki Road Lines',
                'departure_time': '21:20',
                'arrival_time': '06:55',
                'fare': 599,
                'bus_type': 'Non A/C Seater / Sleeper (2+1)',
                'seats_left': 28,
                'live_tracking': True,
                'source': 'MakeMyTrip',
                'duration': '9h 35m'
            },
            {
                'operator': 'KVS Travels',
                'departure_time': '21:30',
                'arrival_time': '06:30',
                'fare': 949,
                'bus_type': 'A/C Sleeper (2+1)',
                'seats_left': 27,
                'live_tracking': True,
                'source': 'MakeMyTrip',
                'duration': '9h 00m'
            },
            {
                'operator': 'PRM Roadways',
                'departure_time': '20:40',
                'arrival_time': '06:00',
                'fare': 949,
                'bus_type': 'A/C Sleeper (2+1)',
                'seats_left': 22,
                'live_tracking': True,
                'source': 'MakeMyTrip',
                'duration': '9h 20m'
            }
        ]
    }
    
    route_key = (origin, destination)
    reverse_key = (destination, origin)
    
    return real_data.get(route_key) or real_data.get(reverse_key)

def fetch_redbus_data(origin, destination, travel_date):
    """
    Fetch data from RedBus-like platforms (using real data when available)
    """
    # Check if we have real data for this route
    real_buses = get_real_bus_data(origin, destination)
    
    if real_buses:
        buses = []
        route_details = get_route_details(origin, destination)
        via_stops = route_details['via_stops'] if route_details else []
        
        amenities_map = {
            'AC Sleeper': ['WiFi', 'Charging', 'Blanket', 'Snacks', 'Pillow'],
            'A/C Sleeper': ['WiFi', 'Charging', 'Blanket', 'Snacks', 'Pillow'],
            'A/C Sleeper (2+1)': ['WiFi', 'Charging', 'Blanket', 'Snacks', 'Pillow'],
            'Non A/C Seater': ['WiFi', 'Charging'],
            'Non A/C Seater/Sleeper': ['WiFi', 'Charging', 'Blanket'],
            'Non-A/C Seater/Sleeper (2+1)': ['WiFi', 'Charging', 'Blanket'],
            'Non-A/C Sleeper (2+1)': ['WiFi', 'Charging', 'Blanket', 'Snacks'],
            'Sleeper/AC': ['WiFi', 'Charging', 'Blanket', 'Snacks'],
            'Non A/C Seater / Sleeper (2+1)': ['WiFi', 'Charging', 'Blanket']
        }
        
        for bus_data in real_buses:
            # Determine amenities based on bus type
            amenities = amenities_map.get(bus_data['bus_type'], ['WiFi', 'Charging', 'Blanket'])
            
            # Calculate total seats (assuming 40-45 seats)
            seats_left = bus_data.get('seats_left') or random.randint(15, 35)
            total_seats = random.choice([40, 42, 45])
            seat_availability = min(seats_left, total_seats - 5)
            
            # Calculate rating based on operator
            operator = bus_data['operator']
            if operator in ['Royal Roadlinks', 'Royal Travels', 'SBM TRANSPORT', 'KVS Travels', 'PSS Transport']:
                rating = round(random.uniform(4.2, 4.8), 1)
            elif operator in ['National Travels CHN', 'LION Travels', 'PRM Roadways', 'A1 Travels']:
                rating = round(random.uniform(3.8, 4.5), 1)
            else:
                rating = round(random.uniform(3.5, 4.3), 1)
            
            # Get duration from data or calculate from times
            if 'duration' in bus_data and bus_data['duration']:
                duration_str = bus_data['duration']
            else:
                dep_time_obj = datetime.strptime(bus_data['departure_time'], '%H:%M').time()
                arr_time_obj = datetime.strptime(bus_data['arrival_time'], '%H:%M').time()
                dep_dt = datetime.combine(date.today(), dep_time_obj)
                arr_dt = datetime.combine(date.today(), arr_time_obj)
                if arr_dt < dep_dt:
                    arr_dt += timedelta(days=1)
                duration_minutes = int((arr_dt - dep_dt).total_seconds() / 60)
                hours = duration_minutes // 60
                minutes = duration_minutes % 60
                duration_str = f"{hours}h {minutes}m"
            
            buses.append({
                'operator_name': operator,
                'bus_number': f"{random.choice(['TN', 'KL', 'AP', 'KA'])}{random.randint(10, 99)}-{random.randint(1000, 9999)}",
                'route_name': f"{origin} to {destination}",
                'origin': origin,
                'destination': destination,
                'departure_time': bus_data['departure_time'],
                'arrival_time': bus_data['arrival_time'],
                'via_stops': via_stops,
                'fare': bus_data['fare'],
                'bus_type': bus_data['bus_type'],
                'amenities': amenities,
                'seat_availability': seat_availability,
                'total_seats': total_seats,
                'rating': rating,
                'platform_source': 'redbus' if 'redBus' in bus_data.get('source', '') else 'makemytrip' if 'MakeMyTrip' in bus_data.get('source', '') else 'abhibus' if 'AbhiBus' in bus_data.get('source', '') else 'paytm' if 'Paytm' in bus_data.get('source', '') else 'abhibus',
                'booking_url': f"https://www.redbus.in/bus-tickets/{origin.lower().replace(' ', '-')}-to-{destination.lower().replace(' ', '-')}",
                'live_tracking': bus_data.get('live_tracking', True),
                'duration': duration_str
            })
        
        return buses
    
    # Fallback to comprehensive list if no real data
    operators = [
        'KPN Travels', 'SRS Travels', 'Parveen Travels', 'Kallada Travels', 
        'Orange Travels', 'VRL Travels', 'IntrCity SmartBus', 'Morning Star Travels',
        'National Travels', 'Jeeva Travels', 'Royal Travels', 'Royal Road Links',
        'SRS Vee Vee', 'SRS Travels Express', 'Diwakar Travels', 'Kallada Travels Express',
        'KPN Travels Express', 'Orange Tours & Travels', 'Parveen Travels Express',
        'Morning Star Travels Express', 'National Travels Express', 'Jeeva Travels Express',
        'Royal Travels Express', 'Royal Road Links Express', 'SRS Vee Vee Express',
        'Diwakar Travels Express', 'KPN Tours', 'SRS Tours', 'Parveen Tours',
        'Kallada Tours', 'Orange Tours', 'VRL Tours', 'National Tours',
        'Jeeva Tours', 'Royal Tours', 'Royal Road Links Tours', 'SRS Vee Vee Tours',
        'Diwakar Tours', 'Morning Star Tours', 'KPN Super', 'SRS Super',
        'Parveen Super', 'Kallada Super', 'Orange Super', 'VRL Super'
    ]
    
    bus_types = ['AC Sleeper', 'Non-AC Seater', 'AC Seater', 'Non-AC Sleeper', 
                'Multi-Axle AC', 'Volvo AC Sleeper', 'AC Semi-Sleeper', 'Non-AC Semi-Sleeper',
                'Volvo Multi-Axle', 'AC Sleeper (2+1)', 'Non-AC Sleeper (2+1)']
    
    amenities_list = [
        ['WiFi', 'Charging', 'Blanket'],
        ['WiFi', 'Charging', 'Blanket', 'Snacks'],
        ['WiFi', 'Charging', 'Blanket', 'Snacks', 'Pillow'],
        ['WiFi', 'Charging', 'Blanket', 'Snacks', 'Pillow', 'Water Bottle'],
        ['WiFi', 'Charging'],
        ['WiFi', 'Charging', 'Blanket', 'Snacks', 'Pillow', 'Water Bottle', 'Entertainment']
    ]
    
    route_details = get_route_details(origin, destination)
    
    buses = []
    num_buses = random.randint(4, 8)
    selected_operators = random.sample(operators, min(num_buses, len(operators)))
    
    for i, operator in enumerate(selected_operators):
        if route_details:
            dep_time = route_details['departure_times'][i % len(route_details['departure_times'])]
            arr_time = route_details['arrival_times'][i % len(route_details['arrival_times'])]
            via_stops = route_details['via_stops']
        else:
            dep_hour = random.randint(18, 23)
            dep_min = random.choice([0, 15, 30, 45])
            arrival_hour = (dep_hour + random.randint(8, 12)) % 24
            arrival_min = random.choice([0, 15, 30, 45])
            dep_time = f"{dep_hour:02d}:{dep_min:02d}"
            arr_time = f"{arrival_hour:02d}:{arrival_min:02d}"
            via_stops = []
        
        bus_type = random.choice(bus_types)
        fare = calculate_fare(origin, destination, bus_type)
        
        if operator in ['KPN Travels', 'SRS Travels', 'Parveen Travels', 'Kallada Travels']:
            rating = round(random.uniform(4.0, 4.8), 1)
        elif operator in ['National Travels', 'Jeeva Travels', 'Royal Travels', 'Royal Road Links', 'SRS Vee Vee']:
            rating = round(random.uniform(3.8, 4.6), 1)
        else:
            rating = round(random.uniform(3.5, 4.5), 1)
        
        buses.append({
            'operator_name': operator,
            'bus_number': f"{random.choice(['TN', 'KL', 'AP', 'KA'])}{random.randint(10, 99)}-{random.randint(1000, 9999)}",
            'route_name': f"{origin} to {destination}",
            'origin': origin,
            'destination': destination,
            'departure_time': dep_time,
            'arrival_time': arr_time,
            'via_stops': via_stops,
            'fare': fare,
            'bus_type': bus_type,
            'amenities': random.choice(amenities_list),
            'seat_availability': random.randint(5, 35),
            'total_seats': 40 if 'Sleeper' in bus_type else 45,
            'rating': rating,
            'platform_source': 'redbus',
            'booking_url': f"https://www.redbus.in/bus-tickets/{origin.lower().replace(' ', '-')}-to-{destination.lower().replace(' ', '-')}",
            'live_tracking': True,
            'duration': None
        })
    
    return buses

def get_route_details(origin, destination):
    """Get specific route details with timings and stops"""
    route_key = f"{origin}_{destination}"
    
    route_map = {
        'Srivilliputhur_Bengaluru': {
            'departure_times': ['18:00', '19:00', '19:30', '20:00', '20:30', '21:00'],
            'arrival_times': ['05:00', '06:00', '06:30', '07:00', '07:30', '08:00'],
            'via_stops': ['Krishnankovil', 'Madurai', 'Dindugal', 'Salem', 'Hosur']
        },
        'Srivilliputhur_Chennai': {
            'departure_times': ['18:30', '19:00', '19:30', '20:00', '20:30', '21:00'],
            'arrival_times': ['05:30', '06:00', '06:30', '07:00', '07:30', '08:00'],
            'via_stops': ['Krishnankovil', 'Madurai', 'Trichy', 'Vellore']
        },
        'Srivilliputhur_Madurai': {
            'departure_times': ['06:00', '07:00', '08:00', '09:00', '10:00', '14:00', '16:00', '18:00'],
            'arrival_times': ['08:00', '09:00', '10:00', '11:00', '12:00', '16:00', '18:00', '20:00'],
            'via_stops': ['Krishnankovil', 'Alagapuri', 'T. Kalupatti', 'Tirumangalam']
        },
        'Srivilliputhur_Coimbatore': {
            'departure_times': ['07:00', '08:00', '09:00', '10:00', '14:00', '16:00'],
            'arrival_times': ['12:00', '13:00', '14:00', '15:00', '19:00', '21:00'],
            'via_stops': ['Sivakasi', 'Virudhunagar', 'Dindugal']
        },
        'Krishnankovil_Bengaluru': {
            'departure_times': ['19:00', '19:30', '20:00', '20:30', '21:00'],
            'arrival_times': ['06:00', '06:30', '07:00', '07:30', '08:00'],
            'via_stops': ['Madurai', 'Dindugal', 'Salem', 'Hosur']
        },
        'Krishnankovil_Chennai': {
            'departure_times': ['19:00', '19:30', '20:00', '20:30', '21:00'],
            'arrival_times': ['06:00', '06:30', '07:00', '07:30', '08:00'],
            'via_stops': ['Madurai', 'Trichy', 'Vellore']
        },
        'Madurai_Bengaluru': {
            'departure_times': ['18:00', '18:30', '19:00', '19:30', '20:00', '20:30', '21:00'],
            'arrival_times': ['05:00', '05:30', '06:00', '06:30', '07:00', '07:30', '08:00'],
            'via_stops': ['Dindugal', 'Salem', 'Hosur']
        },
        'Madurai_Chennai': {
            'departure_times': ['18:30', '19:00', '19:30', '20:00', '20:30', '21:00', '21:30'],
            'arrival_times': ['05:30', '06:00', '06:30', '07:00', '07:30', '08:00', '08:30'],
            'via_stops': ['Trichy', 'Vellore']
        },
        'Madurai_Coimbatore': {
            'departure_times': ['06:00', '07:00', '08:00', '09:00', '14:00', '16:00', '18:00'],
            'arrival_times': ['10:00', '11:00', '12:00', '13:00', '18:00', '20:00', '22:00'],
            'via_stops': ['Dindugal', 'Palani']
        },
        'Chennai_Bengaluru': {
            'departure_times': ['21:00', '21:30', '22:00', '22:30', '23:00', '23:30'],
            'arrival_times': ['05:00', '05:30', '06:00', '06:30', '07:00', '07:30'],
            'via_stops': ['Vellore', 'Krishnagiri']
        },
        'Chennai_Coimbatore': {
            'departure_times': ['20:00', '20:30', '21:00', '21:30', '22:00'],
            'arrival_times': ['06:00', '06:30', '07:00', '07:30', '08:00'],
            'via_stops': ['Salem', 'Erode']
        },
        'Coimbatore_Bengaluru': {
            'departure_times': ['19:00', '19:30', '20:00', '20:30', '21:00', '21:30'],
            'arrival_times': ['04:00', '04:30', '05:00', '05:30', '06:00', '06:30'],
            'via_stops': ['Salem', 'Hosur']
        },
        'Dindugal_Madurai': {
            'departure_times': ['05:00', '06:00', '07:00', '08:00', '09:00', '10:00', '14:00', '16:00'],
            'arrival_times': ['06:30', '07:30', '08:30', '09:30', '10:30', '11:30', '15:30', '17:30'],
            'via_stops': ['Palani']
        },
        'Dindugal_Coimbatore': {
            'departure_times': ['06:00', '07:00', '08:00', '09:00', '14:00', '16:00'],
            'arrival_times': ['09:30', '10:30', '11:30', '12:30', '17:30', '19:30'],
            'via_stops': ['Palani']
        },
        'Sivakasi_Madurai': {
            'departure_times': ['05:00', '06:00', '07:00', '08:00', '09:00', '10:00', '14:00', '16:00'],
            'arrival_times': ['07:00', '08:00', '09:00', '10:00', '11:00', '12:00', '16:00', '18:00'],
            'via_stops': ['Virudhunagar']
        },
        'Sivakasi_Chennai': {
            'departure_times': ['19:00', '19:30', '20:00', '20:30', '21:00'],
            'arrival_times': ['06:00', '06:30', '07:00', '07:30', '08:00'],
            'via_stops': ['Madurai', 'Trichy']
        }
    }
    
    return route_map.get(route_key)

def calculate_fare(origin, destination, bus_type):
    """Calculate fare based on route distance and bus type"""
    # Base distances (in km) - approximate
    distances = {
        ('Srivilliputhur', 'Bengaluru'): 550,
        ('Srivilliputhur', 'Chennai'): 500,
        ('Srivilliputhur', 'Madurai'): 80,
        ('Srivilliputhur', 'Coimbatore'): 250,
        ('Krishnankovil', 'Bengaluru'): 520,
        ('Krishnankovil', 'Chennai'): 470,
        ('Madurai', 'Bengaluru'): 480,
        ('Madurai', 'Chennai'): 450,
        ('Madurai', 'Coimbatore'): 200,
        ('Chennai', 'Bengaluru'): 350,
        ('Chennai', 'Coimbatore'): 520,
        ('Coimbatore', 'Bengaluru'): 360,
        ('Dindugal', 'Madurai'): 60,
        ('Dindugal', 'Coimbatore'): 140,
        ('Sivakasi', 'Madurai'): 50,
        ('Sivakasi', 'Chennai'): 520
    }
    
    route_key = (origin, destination)
    reverse_key = (destination, origin)
    
    distance = distances.get(route_key) or distances.get(reverse_key, 300)
    
    # Fare per km based on bus type
    if 'Volvo' in bus_type or 'Multi-Axle' in bus_type:
        fare_per_km = 2.5
    elif 'AC Sleeper' in bus_type:
        fare_per_km = 2.0
    elif 'AC' in bus_type:
        fare_per_km = 1.5
    else:
        fare_per_km = 1.0
    
    base_fare = int(distance * fare_per_km)
    
    # Add some variation
    variation = random.randint(-100, 200)
    return max(300, base_fare + variation)

def initialize_local_buses():
    """Initialize local bus services for focus cities"""
    focus_cities = {
        'Srivilliputhur': {'lat': 9.5121, 'lng': 77.6336},
        'Krishnankovil': {'lat': 9.572774, 'lng': 77.678473},
        'Madurai': {'lat': 9.9252, 'lng': 78.1198},
        'Chennai': {'lat': 13.0827, 'lng': 80.2707},
        'Bengaluru': {'lat': 12.9716, 'lng': 77.5946},
        'Coimbatore': {'lat': 11.0168, 'lng': 76.9558},
        'Dindugal': {'lat': 10.3629, 'lng': 77.9750},
        'Sivakasi': {'lat': 9.4492, 'lng': 77.7974}
    }
    
    routes = [
        # Srivilliputhur routes
        {'origin': 'Srivilliputhur', 'destination': 'Madurai', 'route': 'SVP-001', 
         'dep': '06:00', 'arr': '08:30', 'via': ['Krishnankovil', 'Alagapuri', 'T. Kalupatti', 'Tirumangalam']},
        {'origin': 'Srivilliputhur', 'destination': 'Chennai', 'route': 'SVP-002',
         'dep': '19:00', 'arr': '06:00', 'via': ['Madurai', 'Trichy', 'Vellore']},
        {'origin': 'Srivilliputhur', 'destination': 'Coimbatore', 'route': 'SVP-003',
         'dep': '08:00', 'arr': '13:00', 'via': ['Sivakasi', 'Virudhunagar', 'Dindugal']},
        
        # Krishnankovil routes
        {'origin': 'Krishnankovil', 'destination': 'Madurai', 'route': 'KNK-001',
         'dep': '06:30', 'arr': '08:00', 'via': ['Alagapuri', 'T. Kalupatti']},
        {'origin': 'Krishnankovil', 'destination': 'Bengaluru', 'route': 'KNK-002',
         'dep': '20:00', 'arr': '07:00', 'via': ['Madurai', 'Salem', 'Hosur']},
        
        # Madurai routes
        {'origin': 'Madurai', 'destination': 'Chennai', 'route': 'MDU-001',
         'dep': '19:00', 'arr': '06:30', 'via': ['Trichy', 'Vellore']},
        {'origin': 'Madurai', 'destination': 'Bengaluru', 'route': 'MDU-002',
         'dep': '18:00', 'arr': '06:00', 'via': ['Dindugal', 'Salem', 'Hosur']},
        {'origin': 'Madurai', 'destination': 'Coimbatore', 'route': 'MDU-003',
         'dep': '07:00', 'arr': '12:00', 'via': ['Dindugal', 'Palani']},
        
        # Chennai routes
        {'origin': 'Chennai', 'destination': 'Bengaluru', 'route': 'CHE-001',
         'dep': '22:00', 'arr': '06:00', 'via': ['Vellore', 'Krishnagiri']},
        {'origin': 'Chennai', 'destination': 'Coimbatore', 'route': 'CHE-002',
         'dep': '21:00', 'arr': '07:00', 'via': ['Salem', 'Erode']},
        
        # Coimbatore routes
        {'origin': 'Coimbatore', 'destination': 'Bengaluru', 'route': 'CBE-001',
         'dep': '20:00', 'arr': '05:00', 'via': ['Salem', 'Hosur']},
        
        # Dindugal routes
        {'origin': 'Dindugal', 'destination': 'Madurai', 'route': 'DGL-001',
         'dep': '06:00', 'arr': '08:00', 'via': ['Palani']},
        {'origin': 'Dindugal', 'destination': 'Coimbatore', 'route': 'DGL-002',
         'dep': '07:00', 'arr': '11:00', 'via': ['Palani']},
        
        # Sivakasi routes
        {'origin': 'Sivakasi', 'destination': 'Madurai', 'route': 'SVK-001',
         'dep': '06:00', 'arr': '08:30', 'via': ['Virudhunagar']},
        {'origin': 'Sivakasi', 'destination': 'Chennai', 'route': 'SVK-002',
         'dep': '19:30', 'arr': '06:30', 'via': ['Madurai', 'Trichy']}
    ]
    
    operators = ['TNSTC', 'SETC', 'MTC']
    bus_types = ['Express', 'Superfast', 'Deluxe', 'Ultra Deluxe']
    
    for route_data in routes:
        existing = LocalBus.query.filter_by(
            route_number=route_data['route'],
            origin=route_data['origin'],
            destination=route_data['destination']
        ).first()
        
        if not existing:
            bus = LocalBus(
                bus_number=f"{route_data['route']}-{random.randint(1, 50)}",
                route_number=route_data['route'],
                operator=random.choice(operators),
                origin=route_data['origin'],
                destination=route_data['destination'],
                departure_time=datetime.strptime(route_data['dep'], '%H:%M').time(),
                arrival_time=datetime.strptime(route_data['arr'], '%H:%M').time(),
                via_stops=route_data.get('via', []),
                fare=random.randint(100, 500),
                bus_type=random.choice(bus_types),
                seat_availability=random.randint(10, 40),
                total_seats=random.choice([45, 50, 55]),
                status='scheduled'
            )
            db.session.add(bus)
    
    db.session.commit()

def initialize_private_operators():
    """Initialize private bus operators for focus cities with comprehensive routes"""
    focus_routes = [
        {'origin': 'Srivilliputhur', 'dest': 'Madurai'},
        {'origin': 'Srivilliputhur', 'dest': 'Chennai'},
        {'origin': 'Srivilliputhur', 'dest': 'Bengaluru'},
        {'origin': 'Srivilliputhur', 'dest': 'Coimbatore'},
        {'origin': 'Krishnankovil', 'dest': 'Madurai'},
        {'origin': 'Krishnankovil', 'dest': 'Chennai'},
        {'origin': 'Krishnankovil', 'dest': 'Bengaluru'},
        {'origin': 'Madurai', 'dest': 'Chennai'},
        {'origin': 'Madurai', 'dest': 'Bengaluru'},
        {'origin': 'Madurai', 'dest': 'Coimbatore'},
        {'origin': 'Chennai', 'dest': 'Bengaluru'},
        {'origin': 'Chennai', 'dest': 'Coimbatore'},
        {'origin': 'Coimbatore', 'dest': 'Bengaluru'},
        {'origin': 'Dindugal', 'dest': 'Madurai'},
        {'origin': 'Dindugal', 'dest': 'Coimbatore'},
        {'origin': 'Sivakasi', 'dest': 'Madurai'},
        {'origin': 'Sivakasi', 'dest': 'Chennai'},
        # Reverse routes
        {'origin': 'Madurai', 'dest': 'Srivilliputhur'},
        {'origin': 'Chennai', 'dest': 'Srivilliputhur'},
        {'origin': 'Bengaluru', 'dest': 'Srivilliputhur'},
        {'origin': 'Coimbatore', 'dest': 'Srivilliputhur'},
        {'origin': 'Madurai', 'dest': 'Krishnankovil'},
        {'origin': 'Chennai', 'dest': 'Krishnankovil'},
        {'origin': 'Bengaluru', 'dest': 'Krishnankovil'},
        {'origin': 'Bengaluru', 'dest': 'Madurai'},
        {'origin': 'Chennai', 'dest': 'Madurai'},
        {'origin': 'Coimbatore', 'dest': 'Madurai'},
        {'origin': 'Bengaluru', 'dest': 'Chennai'},
        {'origin': 'Coimbatore', 'dest': 'Chennai'},
        {'origin': 'Bengaluru', 'dest': 'Coimbatore'},
        {'origin': 'Madurai', 'dest': 'Dindugal'},
        {'origin': 'Coimbatore', 'dest': 'Dindugal'},
        {'origin': 'Madurai', 'dest': 'Sivakasi'},
        {'origin': 'Chennai', 'dest': 'Sivakasi'}
    ]
    
    for route in focus_routes:
        # Fetch RedBus-like data with comprehensive details
        buses = fetch_redbus_data(route['origin'], route['dest'], date.today())
        
        for bus_data in buses:
            # Convert time strings to time objects
            dep_time = datetime.strptime(bus_data['departure_time'], '%H:%M').time()
            arr_time = datetime.strptime(bus_data['arrival_time'], '%H:%M').time()
            
            existing = PrivateOperator.query.filter_by(
                operator_name=bus_data['operator_name'],
                origin=route['origin'],
                destination=route['dest'],
                departure_time=dep_time
            ).first()
            
            if not existing:
                # Calculate duration if available
                duration = None
                if 'duration' in bus_data:
                    duration = bus_data['duration']
                else:
                    # Calculate duration from times
                    dep_dt = datetime.combine(date.today(), dep_time)
                    arr_dt = datetime.combine(date.today(), arr_time)
                    if arr_dt < dep_dt:
                        arr_dt += timedelta(days=1)
                    duration_minutes = int((arr_dt - dep_dt).total_seconds() / 60)
                    hours = duration_minutes // 60
                    minutes = duration_minutes % 60
                    duration = f"{hours}h {minutes}m"
                
                private_bus = PrivateOperator(
                    operator_name=bus_data['operator_name'],
                    bus_number=bus_data['bus_number'],
                    route_name=bus_data['route_name'],
                    origin=bus_data['origin'],
                    destination=bus_data['destination'],
                    departure_time=dep_time,
                    arrival_time=arr_time,
                    via_stops=bus_data.get('via_stops', []),
                    fare=bus_data['fare'],
                    bus_type=bus_data['bus_type'],
                    amenities=bus_data.get('amenities', []),
                    seat_availability=bus_data['seat_availability'],
                    total_seats=bus_data['total_seats'],
                    rating=bus_data['rating'],
                    platform_source=bus_data['platform_source'],
                    booking_url=bus_data['booking_url'],
                    live_tracking=bus_data.get('live_tracking', True),
                    duration=duration
                )
                db.session.add(private_bus)
    
    db.session.commit()

def update_realtime_status():
    """Update real-time bus status (simulated GPS tracking)"""
    # Update local buses
    local_buses = LocalBus.query.filter_by(is_active=True).all()
    for bus in local_buses:
        # Simulate real-time tracking
        status = RealTimeBusStatus.query.filter_by(bus_id=bus.id, bus_type='local').first()
        
        current_time = datetime.now().time()
        if bus.departure_time <= current_time <= bus.arrival_time:
            # Bus is running
            delay = random.randint(0, 30)  # Random delay
            locations = ['On Route', 'Near Next Stop', 'At Stop', 'En Route']
            
            if not status:
                status = RealTimeBusStatus(bus_id=bus.id, bus_type='local')
                db.session.add(status)
            
            status.current_location = random.choice(locations)
            status.delay_minutes = delay
            status.occupancy = bus.total_seats - bus.seat_availability
            status.speed = random.randint(40, 70)
            status.last_updated = datetime.utcnow()
            
            bus.status = 'delayed' if delay > 10 else 'on_time'
            bus.delay_minutes = delay
            bus.last_updated = datetime.utcnow()
    
    # Update private operators
    private_buses = PrivateOperator.query.filter_by(is_active=True).all()
    for bus in private_buses:
        status = RealTimeBusStatus.query.filter_by(bus_id=bus.id, bus_type='private').first()
        
        current_time = datetime.now().time()
        if bus.departure_time <= current_time <= bus.arrival_time:
            delay = random.randint(0, 20)
            
            if not status:
                status = RealTimeBusStatus(bus_id=bus.id, bus_type='private')
                db.session.add(status)
            
            status.current_location = random.choice(['On Highway', 'Approaching Stop', 'At Terminal'])
            status.delay_minutes = delay
            status.occupancy = bus.total_seats - bus.seat_availability
            status.speed = random.randint(50, 80)
            status.last_updated = datetime.utcnow()
            
            bus.status = 'running' if delay < 5 else 'delayed'
            bus.delay_minutes = delay
            bus.last_updated = datetime.utcnow()
    
    db.session.commit()

@app.route('/admin/init-bus-services', methods=['POST'])
@login_required
def init_bus_services():
    """Initialize local buses and private operators"""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        initialize_local_buses()
        initialize_private_operators()
        flash('Bus services initialized successfully!', 'success')
    except Exception as e:
        flash(f'Error initializing bus services: {str(e)}', 'danger')
    
    return redirect(url_for('multi_modal_coordination'))

@app.route('/api/realtime-bus-status/<int:bus_id>')
def get_realtime_bus_status(bus_id):
    """Get real-time status for a specific bus"""
    bus_type = request.args.get('type', 'local')  # local, private, service
    
    if bus_type == 'local':
        bus = LocalBus.query.get(bus_id)
        status = RealTimeBusStatus.query.filter_by(bus_id=bus_id, bus_type='local').first()
    elif bus_type == 'private':
        bus = PrivateOperator.query.get(bus_id)
        status = RealTimeBusStatus.query.filter_by(bus_id=bus_id, bus_type='private').first()
    else:
        bus = BusService.query.get(bus_id)
        status = RealTimeBusStatus.query.filter_by(bus_id=bus_id, bus_type='service').first()
    
    if not bus:
        return jsonify({'error': 'Bus not found'}), 404
    
    # Update status if needed
    if not status or (datetime.utcnow() - status.last_updated).total_seconds() > 60:
        update_realtime_status()
        status = RealTimeBusStatus.query.filter_by(bus_id=bus_id, bus_type=bus_type).first()
    
    return jsonify({
        'bus': bus.to_dict() if hasattr(bus, 'to_dict') else str(bus),
        'realtime_status': status.to_dict() if status else None
    })

@app.route('/api/refresh-realtime', methods=['POST'])
@login_required
def refresh_realtime():
    """Manually refresh real-time status"""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    update_realtime_status()
    return jsonify({'success': True, 'message': 'Real-time status updated'})

# ==================== TRAVEL GUIDE SYSTEM ====================

def get_coordinates(location_name):
    """
    Get coordinates for a location using OpenStreetMap Nominatim API
    Returns (latitude, longitude) tuple
    """
    # Predefined coordinates for key locations
    location_coords = {
        'Kalasalingam University': (9.7072, 77.5076),
        'Krishnankovil': (9.572774, 77.678473),
        'Krishnankovil Junction': (9.572774, 77.678473),
        'Krishnankovil Bus Stop': (9.572774, 77.678473),
        'Srivilliputhur': (9.5121, 77.6336),
        'Srivilliputhur Bus Stand': (9.5121, 77.6336),
        'Tiruthangal': (9.4833, 77.8167),
        'Tiruthangal Bus Stop': (9.4833, 77.8167),
        'Sivakasi': (9.4492, 77.7974),
        'Sivakasi Bus Stand': (9.4492, 77.7974),
        'MEPCO Schlenk Engineering College': (9.4600, 77.8100),
        'MEPCO College': (9.4600, 77.8100),
        'PSR Engineering College': (9.4500, 77.7900),
        'PSR College': (9.4500, 77.7900),
        'Virudhunagar': (9.5833, 77.9500),
        'Virudhunagar Bus Stand': (9.5833, 77.9500),
        'Madurai': (9.9252, 78.1198),
        'Madurai Bus Stand': (9.9252, 78.1198)
    }
    
    # Try exact match first
    if location_name in location_coords:
        return location_coords[location_name]
    
    # Try partial match
    for key, coords in location_coords.items():
        if location_name.lower() in key.lower() or key.lower() in location_name.lower():
            return coords
    
    # Default fallback (Sivakasi area)
    return (9.4492, 77.7974)

def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two coordinates using Haversine formula (km)"""
    from math import radians, cos, sin, asin, sqrt
    
    R = 6371  # Earth radius in km
    
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    
    return round(R * c, 2)

def plan_travel_route(origin, destination):
    """
    Plan a step-by-step travel route between two locations
    Returns structured route with steps, distances, and instructions
    """
    # Get coordinates
    origin_coords = get_coordinates(origin)
    dest_coords = get_coordinates(destination)
    
    steps = []
    total_distance = 0
    total_time = 0
    
    # Route planning logic based on origin and destination
    if 'Kalasalingam' in origin or 'Krishnankovil' in origin:
        # Step 1: From University to Krishnankovil Bus Stop
        steps.append({
            'step_number': 1,
            'mode': 'walk',
            'instruction': f'Walk or take an auto from {origin} to Krishnankovil Bus Stop',
            'distance_km': 0.8,
            'approx_duration_min': 10,
            'coordinates': [9.572774, 77.678473],
            'landmarks': ['Krishnankovil Junction']
        })
        total_distance += 0.8
        total_time += 10
        
        # Step 2: From Krishnankovil to Srivilliputhur
        steps.append({
            'step_number': 2,
            'mode': 'bus',
            'instruction': 'Board any local bus going toward Srivilliputhur from Krishnankovil Bus Stop',
            'distance_km': 2.0,
            'approx_duration_min': 10,
            'coordinates': [9.5121, 77.6336],
            'bus_frequency': 'Every 15-20 minutes',
            'first_bus': '06:00',
            'last_bus': '21:00',
            'fare': '₹10-15'
        })
        total_distance += 2.0
        total_time += 10
        
        # Step 3: From Srivilliputhur to Sivakasi
        if 'MEPCO' in destination or 'PSR' in destination or 'Sivakasi' in destination:
            steps.append({
                'step_number': 3,
                'mode': 'bus',
                'instruction': 'From Srivilliputhur Bus Stand, board a bus to Sivakasi',
                'distance_km': 20.0,
                'approx_duration_min': 35,
                'coordinates': [9.4492, 77.7974],
                'bus_frequency': 'Every 30 minutes',
                'first_bus': '06:00',
                'last_bus': '20:00',
                'fare': '₹25-30',
                'important_note': 'Get down at Tiruthangal stop if bus passes MEPCO; otherwise go to Sivakasi Bus Stand'
            })
            total_distance += 20.0
            total_time += 35
            
            # Step 4: To MEPCO College
            if 'MEPCO' in destination:
                steps.append({
                    'step_number': 4,
                    'mode': 'bus',
                    'instruction': 'From Sivakasi Bus Stand, take local bus labeled "Sivakasi ↔ MEPCO Nagar" to reach MEPCO College',
                    'distance_km': 4.0,
                    'approx_duration_min': 10,
                    'coordinates': [9.4600, 77.8100],
                    'bus_frequency': 'Every 20 minutes',
                    'first_bus': '06:30',
                    'last_bus': '20:30',
                    'fare': '₹8-10'
                })
                total_distance += 4.0
                total_time += 10
            
            # Step 4: To PSR College
            elif 'PSR' in destination:
                steps.append({
                    'step_number': 4,
                    'mode': 'bus',
                    'instruction': 'From Sivakasi Bus Stand, take bus "Sivakasi ↔ PSR Nagar / Sankarankovil Road" to reach PSR College',
                    'distance_km': 3.5,
                    'approx_duration_min': 8,
                    'coordinates': [9.4500, 77.7900],
                    'bus_frequency': 'Every 25 minutes',
                    'first_bus': '07:00',
                    'last_bus': '20:00',
                    'fare': '₹8-10'
                })
                total_distance += 3.5
                total_time += 8
    
    # Add alternate transport options
    alternate_transport = [
        {
            'mode': 'auto',
            'description': 'Auto-rickshaw available from Krishnankovil to Srivilliputhur',
            'fare': '₹40-50',
            'duration_min': 8
        },
        {
            'mode': 'share_van',
            'description': 'Share van services available from Sivakasi to nearby colleges',
            'fare': '₹15-20',
            'duration_min': 5
        }
    ]
    
    return {
        'from': origin,
        'to': destination,
        'total_distance_km': round(total_distance, 2),
        'estimated_time_minutes': total_time,
        'origin_coordinates': origin_coords,
        'destination_coordinates': dest_coords,
        'steps': steps,
        'alternate_transport': alternate_transport
    }

def get_landmarks_near_route(route_coords, radius_km=5):
    """Get landmarks (tea stalls, ATMs, petrol bunks) near the route"""
    # Predefined landmarks
    all_landmarks = [
        {'name': 'Krishnankovil Tea Stall', 'type': 'tea_stall', 'location': 'Krishnankovil', 'lat': 9.572774, 'lng': 77.678473},
        {'name': 'Srivilliputhur ATM', 'type': 'atm', 'location': 'Srivilliputhur', 'lat': 9.5121, 'lng': 77.6336},
        {'name': 'Srivilliputhur Petrol Bunk', 'type': 'petrol_bunk', 'location': 'Srivilliputhur', 'lat': 9.5150, 'lng': 77.6350},
        {'name': 'Tiruthangal Tea Stall', 'type': 'tea_stall', 'location': 'Tiruthangal', 'lat': 9.4833, 'lng': 77.8167},
        {'name': 'Sivakasi Bus Stand Tea Stall', 'type': 'tea_stall', 'location': 'Sivakasi', 'lat': 9.4492, 'lng': 77.7974},
        {'name': 'Sivakasi ATM', 'type': 'atm', 'location': 'Sivakasi', 'lat': 9.4500, 'lng': 77.8000},
        {'name': 'MEPCO Road Tea Stall', 'type': 'tea_stall', 'location': 'Sivakasi', 'lat': 9.4550, 'lng': 77.8050},
        {'name': 'PSR Road Petrol Bunk', 'type': 'petrol_bunk', 'location': 'Sivakasi', 'lat': 9.4480, 'lng': 77.7920}
    ]
    
    # Filter landmarks near route (simplified - in production, calculate distance)
    nearby_landmarks = []
    for landmark in all_landmarks:
        # Check if landmark is within radius (simplified check)
        for step in route_coords:
            if step:
                dist = calculate_distance(landmark['lat'], landmark['lng'], step[0], step[1])
                if dist <= radius_km:
                    nearby_landmarks.append(landmark)
                    break
    
    return nearby_landmarks

def generate_safety_tips(route_data, weather):
    """Generate safety and travel tips"""
    tips = []
    
    # Weather-based tips
    if weather.get('condition') == 'rainy':
        tips.append('🌧️ Rain expected at destination. Carry an umbrella or waterproof bag.')
    elif weather.get('condition') == 'foggy':
        tips.append('🌫️ Fog alert. Expect slight delays and travel carefully.')
    elif weather.get('condition') == 'sunny':
        tips.append('☀️ Sunny weather. Stay hydrated during your journey.')
    
    # Time-based tips
    if route_data['estimated_time_minutes'] > 60:
        tips.append('⏰ Long journey ahead. Pack snacks and water.')
    
    # General safety tips
    tips.append('🚌 Keep your ticket handy and arrive at bus stop 5 minutes early.')
    tips.append('📱 Keep your phone charged for navigation and emergency contacts.')
    tips.append('💰 Carry small change for bus fares and local transport.')
    
    return tips

@app.route('/travel-guide')
def travel_guide():
    """Travel Guide System - Main page"""
    # Get all educational institutions
    institutions = EducationalInstitution.query.all()
    
    # Get popular routes
    popular_routes = TravelRoute.query.order_by(TravelRoute.created_at.desc()).limit(5).all()
    
    return render_template('travel_guide/index.html',
                         institutions=institutions,
                         popular_routes=popular_routes)

@app.route('/travel-guide/plan', methods=['GET', 'POST'])
def travel_guide_plan():
    """Plan a travel route"""
    if request.method == 'POST':
        origin = request.form.get('origin', '').strip()
        destination = request.form.get('destination', '').strip()
        
        if not all([origin, destination]):
            return jsonify({'error': 'Origin and destination required'}), 400
        
        # Plan the route
        route_data = plan_travel_route(origin, destination)
        
        # Get weather for destination
        weather = get_weather_data(destination.split(',')[-1].strip() if ',' in destination else destination, date.today())
        
        # Get landmarks near route
        route_coords = [route_data['origin_coordinates'], route_data['destination_coordinates']]
        for step in route_data['steps']:
            if 'coordinates' in step:
                route_coords.append((step['coordinates'][0], step['coordinates'][1]))
        
        landmarks = get_landmarks_near_route(route_coords)
        
        # Save route
        route = TravelRoute(
            route_name=f"{origin} to {destination}",
            origin=origin,
            destination=destination,
            origin_lat=route_data['origin_coordinates'][0],
            origin_lng=route_data['origin_coordinates'][1],
            destination_lat=route_data['destination_coordinates'][0],
            destination_lng=route_data['destination_coordinates'][1],
            total_distance_km=route_data['total_distance_km'],
            estimated_time_minutes=route_data['estimated_time_minutes'],
            route_steps=route_data['steps'],
            alternate_transport=route_data['alternate_transport']
        )
        db.session.add(route)
        db.session.commit()
        
        # Return JSON response
        response_data = {
            **route_data,
            'weather': weather,
            'landmarks': landmarks,
            'safety_tips': generate_safety_tips(route_data, weather),
            'route_id': route.id
        }
        
        return jsonify(response_data)
    
    # Get all institutions for dropdown
    institutions = EducationalInstitution.query.all()
    
    # Pre-fill form if URL parameters are provided
    origin = request.args.get('origin', '')
    destination = request.args.get('destination', '')
    
    return render_template('travel_guide/plan.html', 
                         institutions=institutions,
                         prefill_origin=origin,
                         prefill_destination=destination)

@app.route('/travel-guide/results/<int:route_id>')
def travel_guide_results(route_id):
    """Display travel route results with animated step-by-step guide"""
    route = TravelRoute.query.get_or_404(route_id)
    
    # Get weather for destination
    weather = get_weather_data(route.destination.split(',')[-1].strip() if ',' in route.destination else route.destination, date.today())
    
    # Get landmarks
    route_coords = [(route.origin_lat, route.origin_lng), (route.destination_lat, route.destination_lng)]
    if route.route_steps:
        for step in route.route_steps:
            if 'coordinates' in step:
                route_coords.append((step['coordinates'][0], step['coordinates'][1]))
    
    landmarks = get_landmarks_near_route(route_coords)
    
    route_data = route.to_dict()
    route_data['weather'] = weather
    route_data['landmarks'] = landmarks
    route_data['safety_tips'] = generate_safety_tips(route_data, weather)
    route_data['weather_recommendations'] = get_actionable_weather_recommendations(weather)
    
    return render_template('travel_guide/results.html', route_data=route_data)

@app.route('/passengers/timetables')
def passenger_timetables():
    """Show multi-modal travel options: bus, train, flight, car, bike."""
    origin      = request.args.get('origin', '').strip()
    destination = request.args.get('destination', '').strip()
    travel_date = request.args.get('date', '').strip()

    from datetime import date as _date, timedelta as _td
    _today    = _date.today()
    _max_date = _today + _td(days=90)
    today_str    = _today.strftime('%Y-%m-%d')
    max_date_str = _max_date.strftime('%Y-%m-%d')

    # Normalise common spelling variations so DB lookups always match
    _CITY_ALIASES = {
        'krishnankoil':        'Krishnankovil',
        'krishnan koil':       'Krishnankovil',
        'krishnan kovil':      'Krishnankovil',
        'srivilliputtur':      'Srivilliputhur',
        'srivilliputhur':      'Srivilliputhur',
        'bangalore':           'Bengaluru',
        'bengalore':           'Bengaluru',
        'trivandrum':          'Thiruvananthapuram',
        'bombay':              'Mumbai',
        'calcutta':            'Kolkata',
        'madras':              'Chennai',
        'coimbatore':          'Coimbatore',
        'tirunelveli':         'Tirunelveli',
        'virudhunagar':        'Virudhunagar',
        'rajapalayam':         'Rajapalayam',
        'tenkasi':             'Tenkasi',
    }
    def _norm_city(c):
        return _CITY_ALIASES.get(c.lower(), c.title()) if c else c
    origin      = _norm_city(origin)
    destination = _norm_city(destination)

    # ── Approximate road distances (km) between common South-India cities ──
    _DISTANCES = {
        frozenset(['srivilliputhur','madurai']): 75,
        frozenset(['srivilliputhur','virudhunagar']): 25,
        frozenset(['srivilliputhur','rajapalayam']): 30,
        frozenset(['srivilliputhur','chennai']): 535,
        frozenset(['srivilliputhur','bengaluru']): 510,
        frozenset(['srivilliputhur','coimbatore']): 285,
        frozenset(['srivilliputhur','trichy']): 205,
        frozenset(['srivilliputhur','tirunelveli']): 100,
        frozenset(['srivilliputhur','tenkasi']): 55,
        frozenset(['krishnankovil','madurai']): 65,
        frozenset(['krishnankovil','virudhunagar']): 18,
        frozenset(['krishnankovil','chennai']): 525,
        frozenset(['krishnankovil','bengaluru']): 500,
        frozenset(['krishnankoil','madurai']): 65,
        frozenset(['krishnankoil','virudhunagar']): 18,
        frozenset(['krishnankoil','chennai']): 525,
        frozenset(['krishnankoil','bengaluru']): 500,
        frozenset(['sivakasi','madurai']): 80,
        frozenset(['sivakasi','virudhunagar']): 15,
        frozenset(['sivakasi','chennai']): 540,
        frozenset(['madurai','chennai']): 462,
        frozenset(['madurai','bengaluru']): 450,
        frozenset(['madurai','coimbatore']): 210,
        frozenset(['madurai','trichy']): 132,
        frozenset(['madurai','dindigul']): 65,
        frozenset(['madurai','tirunelveli']): 160,
        frozenset(['madurai','tenkasi']): 120,
        frozenset(['madurai','rajapalayam']): 110,
        frozenset(['madurai','salem']): 230,
        frozenset(['madurai','tirumangalam']): 25,
        frozenset(['chennai','bengaluru']): 346,
        frozenset(['chennai','coimbatore']): 495,
        frozenset(['chennai','trichy']): 332,
        frozenset(['chennai','salem']): 340,
        frozenset(['chennai','vellore']): 138,
        frozenset(['bengaluru','coimbatore']): 364,
        frozenset(['bengaluru','mysuru']): 145,
        frozenset(['bengaluru','hosur']): 45,
        frozenset(['bengaluru','krishnagiri']): 90,
        frozenset(['bengaluru','trichy']): 480,
        frozenset(['coimbatore','trichy']): 220,
        frozenset(['coimbatore','erode']): 80,
        frozenset(['coimbatore','salem']): 160,
        frozenset(['coimbatore','ooty']): 85,
        frozenset(['trichy','thanjavur']): 56,
        frozenset(['trichy','salem']): 182,
        frozenset(['trichy','dindigul']): 100,
        frozenset(['salem','erode']): 60,
        frozenset(['erode','tiruppur']): 42,
        frozenset(['tiruppur','coimbatore']): 55,
        frozenset(['dindigul','coimbatore']): 145,
        frozenset(['tenkasi','tirunelveli']): 60,
        frozenset(['rajapalayam','tirunelveli']): 75,
        frozenset(['hosur','chennai']): 310,
        frozenset(['vellore','bengaluru']): 210,
    }

    def get_travel_info(orig, dest):
        import math as _m
        key = frozenset([orig.lower(), dest.lower()])
        dist = _DISTANCES.get(key)

        # ── Haversine fallback for any city pair not in the hardcoded dict ──
        if not dist:
            c1 = CITY_COORDS.get(orig.lower())
            c2 = CITY_COORDS.get(dest.lower())
            if c1 and c2:
                # straight-line × 1.3 road-winding factor
                R = 6371.0
                dlat = _m.radians(c2[0] - c1[0])
                dlon = _m.radians(c2[1] - c1[1])
                a = (_m.sin(dlat/2)**2 +
                     _m.cos(_m.radians(c1[0])) * _m.cos(_m.radians(c2[0])) *
                     _m.sin(dlon/2)**2)
                straight = R * 2 * _m.asin(_m.sqrt(a))
                dist = round(straight * 1.3)   # road factor

        if not dist:
            return None

        car_min  = int(dist / 60 * 60)   # avg 60 km/h
        bike_min = int(dist / 40 * 60)   # avg 40 km/h
        car_fuel  = round(dist / 15 * 102, 0)   # ~15 km/l petrol ₹102
        bike_fuel = round(dist / 45 * 102, 0)   # ~45 km/l
        return {
            'distance_km': dist,
            'car_hours':  car_min  // 60,
            'car_mins':   car_min  % 60,
            'bike_hours': bike_min // 60,
            'bike_mins':  bike_min % 60,
            'car_fuel_cost':  int(car_fuel),
            'bike_fuel_cost': int(bike_fuel),
        }

    local_buses   = []
    private_buses = []
    trains        = []
    flights       = []
    road_info     = None
    
    if origin and destination:
        ol, dl = origin.lower(), destination.lower()

        local_buses = LocalBus.query.filter(
            LocalBus.is_active == True,
            or_(
                and_(func.lower(LocalBus.origin)==ol, func.lower(LocalBus.destination)==dl),
                and_(func.lower(LocalBus.origin)==dl, func.lower(LocalBus.destination)==ol)
            )
        ).order_by(LocalBus.departure_time).all()
        
        private_buses = PrivateOperator.query.filter(
            PrivateOperator.is_active == True,
            or_(
                and_(func.lower(PrivateOperator.origin)==ol, func.lower(PrivateOperator.destination)==dl),
                and_(func.lower(PrivateOperator.origin)==dl, func.lower(PrivateOperator.destination)==ol)
            )
        ).order_by(PrivateOperator.departure_time).all()

        # Trains are fetched live from erail.in API — not from DB
        trains = []

        flights = FlightSchedule.query.filter(
            FlightSchedule.is_active == True,
            or_(
                and_(func.lower(FlightSchedule.origin_airport).contains(ol),
                     func.lower(FlightSchedule.destination_airport).contains(dl)),
                and_(func.lower(FlightSchedule.origin_airport).contains(dl),
                     func.lower(FlightSchedule.destination_airport).contains(ol))
            )
        ).order_by(FlightSchedule.departure_time).all()

        road_info = get_travel_info(origin, destination)
    else:
        local_buses   = LocalBus.query.filter_by(is_active=True).order_by(LocalBus.departure_time).limit(50).all()
        private_buses = PrivateOperator.query.filter_by(is_active=True).order_by(PrivateOperator.departure_time).limit(50).all()
        trains        = []   # live from erail.in API
        flights       = FlightSchedule.query.filter_by(is_active=True).order_by(FlightSchedule.departure_time).limit(20).all()
    
    all_cities = set()
    for b in LocalBus.query.with_entities(LocalBus.origin, LocalBus.destination).all():
        all_cities.update([b.origin, b.destination])
    for b in PrivateOperator.query.with_entities(PrivateOperator.origin, PrivateOperator.destination).all():
        all_cities.update([b.origin, b.destination])

    from datetime import date as _tdate
    show_live_bus_map = bool(
        origin and destination
        and _gps_route_key(origin, destination) in GPS_ENABLED_ROUTE_KEYS
    )
    route_tourism = None
    if origin and destination:
        route_tourism = tourism_bundle_for_timetables(origin, destination, local_buses)
    return render_template('passengers/timetables.html',
                         local_buses=local_buses,
                         private_buses=private_buses,
                           flights=flights,
                           road_info=road_info,
                         origin=origin,
                         destination=destination,
                           travel_date=travel_date,
                           all_cities=sorted(all_cities),
                           today=today_str,
                           max_date=max_date_str,
                           today_date=today_str,
                           show_live_bus_map=show_live_bus_map,
                           route_tourism=route_tourism)

# ─────────────────────────────────────────────────────────────────────────────
# Weather Forecast Helper
# ─────────────────────────────────────────────────────────────────────────────

def get_weather_forecast(city_name):
    """Get 3-day weather forecast. Uses wttr.in if API key is configured, otherwise demo data."""
    api_key = os.getenv('OPENWEATHER_API_KEY', 'demo')
    if api_key != 'demo' and requests:
        try:
            url = f"https://wttr.in/{city_name},India?format=j1"
            resp = requests.get(url, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                forecast = []
                for day in data.get('weather', [])[:3]:
                    midday = day['hourly'][4] if len(day['hourly']) > 4 else day['hourly'][0]
                    forecast.append({
                        'date': day.get('date', ''),
                        'max_temp': day.get('maxtempC', '--'),
                        'min_temp': day.get('mintempC', '--'),
                        'description': midday['weatherDesc'][0]['value'] if midday.get('weatherDesc') else 'N/A',
                        'rain_chance': midday.get('chanceofrain', 0),
                        'humidity': midday.get('humidity', 0),
                        'wind_kmph': midday.get('windspeedKmph', 0),
                        'uv_index': midday.get('uvIndex', 0),
                    })
                return forecast
        except Exception as e:
            app.logger.warning(f"wttr.in forecast error for {city_name}: {e}")
    # Demo forecast data (realistic for Tamil Nadu climate)
    city_profiles = {
        'Madurai':        {'base': 34, 'desc_cycle': ['Hot & Sunny', 'Partly Cloudy', 'Hot & Sunny'], 'rain': [5, 15, 8],  'uv': [9, 8, 9]},
        'Chennai':        {'base': 35, 'desc_cycle': ['Sunny & Humid', 'Partly Cloudy', 'Light Showers'], 'rain': [10, 20, 35], 'uv': [8, 7, 7]},
        'Bengaluru':      {'base': 28, 'desc_cycle': ['Partly Cloudy', 'Pleasant', 'Light Rain'], 'rain': [20, 15, 40], 'uv': [7, 7, 6]},
        'Tenkasi':        {'base': 30, 'desc_cycle': ['Partly Cloudy', 'Light Rain', 'Overcast'], 'rain': [30, 50, 40], 'uv': [7, 6, 5]},
        'Srivilliputhur': {'base': 33, 'desc_cycle': ['Hot & Sunny', 'Partly Cloudy', 'Sunny'], 'rain': [5, 10, 5], 'uv': [9, 8, 9]},
        'Dindigul':       {'base': 32, 'desc_cycle': ['Sunny', 'Partly Cloudy', 'Clear'], 'rain': [5, 15, 8], 'uv': [9, 8, 9]},
    }
    profile = city_profiles.get(city_name, {'base': 32, 'desc_cycle': ['Partly Cloudy', 'Sunny', 'Clear'], 'rain': [10, 10, 15], 'uv': [8, 8, 8]})
    from datetime import date as _date, timedelta as _td
    return [
        {'date': str(_date.today() + _td(days=i)),
         'max_temp': profile['base'] + [2, 1, 3][i],
         'min_temp': profile['base'] - [6, 7, 5][i],
         'description': profile['desc_cycle'][i],
         'rain_chance': profile['rain'][i],
         'humidity': [65, 70, 68][i],
         'wind_kmph': [12, 10, 14][i],
         'uv_index': profile['uv'][i]} for i in range(3)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Tourism Data & Helper
# ─────────────────────────────────────────────────────────────────────────────

TOURISM_SPOTS = {
    'Madurai': [
        {'name': 'Meenakshi Amman Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Historic twin temples of Meenakshi (Parvati) and Sundareswarar (Shiva) in the old city; fourteen gopurams including the famous south tower. Allow 2–3 hours for darshan on busy days.',
         'distance': '~1 km E of Periyar (Central) bus stand', 'rating': 4.9, 'timings': '5:00 AM – 12:30 PM & 4:00 PM – 10:00 PM', 'entry': 'Free (museum inside paid)'},
        {'name': 'Thirumalai Nayakkar Palace', 'type': 'Heritage', 'emoji': '🏛️',
         'description': 'Impressive 17th-century palace blending Dravidian and Mughal architectural styles.',
         'distance': '1.5 km', 'rating': 4.5, 'timings': '9:00 AM – 5:00 PM', 'entry': '₹50'},
        {'name': 'Gandhi Museum', 'type': 'Museum', 'emoji': '🏛️',
         'description': 'Museum dedicated to Mahatma Gandhi housed in a 17th-century palace.',
         'distance': '2 km', 'rating': 4.3, 'timings': '10:00 AM – 1:00 PM & 2:00 PM – 5:30 PM', 'entry': 'Free'},
        {'name': 'Vandiyur Mariamman Teppakulam', 'type': 'Religious', 'emoji': '💧',
         'description': 'Large tank with a central island temple, famous for float festival.',
         'distance': '3 km', 'rating': 4.4, 'timings': 'Open 24 hrs', 'entry': 'Free'},
        {'name': 'Koodal Azhagar Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Ancient Vishnu temple with three storeys representing three periods of the day.',
         'distance': '0.5 km', 'rating': 4.6, 'timings': '6:00 AM – 12:00 PM & 4:00 PM – 9:00 PM', 'entry': 'Free'},
    ],
    'Chennai': [
        {'name': 'Marina Beach', 'type': 'Beach', 'emoji': '🏖️',
         'description': 'Second longest urban beach in the world, stretching 13 km with golden sand.',
         'distance': '0 km from city', 'rating': 4.6, 'timings': 'Open 24 hrs', 'entry': 'Free'},
        {'name': 'Kapaleeshwarar Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Dravidian-style Shiva temple in Mylapore with colourful sculptures.',
         'distance': '5 km', 'rating': 4.7, 'timings': '5:30 AM – 12:00 PM & 4:00 PM – 9:30 PM', 'entry': 'Free'},
        {'name': 'Government Museum Chennai', 'type': 'Museum', 'emoji': '🏛️',
         'description': 'One of the oldest museums in India with bronze gallery, coins, and manuscripts.',
         'distance': '6 km', 'rating': 4.4, 'timings': '9:30 AM – 5:00 PM (Fri closed)', 'entry': '₹15'},
        {'name': 'Fort St. George', 'type': 'Heritage', 'emoji': '🏰',
         'description': 'First English fortress in India, now houses Tamil Nadu Legislature and museum.',
         'distance': '3 km', 'rating': 4.3, 'timings': '9:00 AM – 5:00 PM (Fri closed)', 'entry': '₹5'},
        {'name': 'Elliot\'s Beach (Besant Nagar)', 'type': 'Beach', 'emoji': '🏖️',
         'description': 'Calm, clean beach with Ashtalakshmi Temple nearby, popular for evening strolls.',
         'distance': '8 km', 'rating': 4.5, 'timings': 'Open 24 hrs', 'entry': 'Free'},
        {'name': 'Mahabalipuram (Day Trip)', 'type': 'UNESCO Heritage', 'emoji': '🗿',
         'description': 'UNESCO World Heritage site with Shore Temple, Pancha Rathas, and Arjuna\'s Penance.',
         'distance': '55 km', 'rating': 4.8, 'timings': '6:00 AM – 6:00 PM', 'entry': '₹40'},
    ],
    'Bengaluru': [
        {'name': 'Lalbagh Botanical Garden', 'type': 'Nature', 'emoji': '🌿',
         'description': '240-acre botanical garden with a 3,000-year-old rock formation and famous glasshouse.',
         'distance': '4 km from MG Road', 'rating': 4.7, 'timings': '6:00 AM – 7:00 PM', 'entry': '₹20'},
        {'name': 'Bangalore Palace', 'type': 'Heritage', 'emoji': '🏰',
         'description': 'Tudor-style palace modelled on Windsor Castle with beautiful fortified towers.',
         'distance': '3 km', 'rating': 4.4, 'timings': '10:00 AM – 5:30 PM', 'entry': '₹230'},
        {'name': 'Vidhana Soudha', 'type': 'Architecture', 'emoji': '🏛️',
         'description': 'Grand seat of Karnataka Legislature – an icon of neo-Dravidian architecture.',
         'distance': '2 km', 'rating': 4.6, 'timings': 'Exterior view only', 'entry': 'Free'},
        {'name': 'Cubbon Park', 'type': 'Nature', 'emoji': '🌳',
         'description': '300-acre lungs of the city with jogging tracks, statues, and heritage buildings.',
         'distance': '2 km', 'rating': 4.7, 'timings': '6:00 AM – 6:00 PM', 'entry': 'Free'},
        {'name': 'ISKCON Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'One of the largest ISKCON temples in the world with stunning architecture.',
         'distance': '10 km', 'rating': 4.8, 'timings': '7:15 AM – 1:00 PM & 4:15 PM – 8:30 PM', 'entry': 'Free'},
        {'name': 'Wonderla Amusement Park', 'type': 'Entertainment', 'emoji': '🎢',
         'description': 'Popular amusement park with 60+ land and water rides.',
         'distance': '28 km', 'rating': 4.6, 'timings': '11:00 AM – 6:00 PM', 'entry': '₹1,050'},
    ],
    'Krishnankovil': [
        {'name': 'Kalasalingam area & Krishnankovil Vishnu Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Krishnankovil is best known as the seat of Kalasalingam Academy of Research and Education; the local Vishnu temple is a traditional stop for visitors to the campus belt between Srivilliputhur and Virudhunagar.',
         'distance': 'At town centre / campus approach', 'rating': 4.8, 'timings': '6:00 AM – 12:00 PM & 4:00 PM – 9:00 PM', 'entry': 'Free'},
        {'name': 'Srivilliputhur Andal Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': '11th-century Vaishnavite shrine of Andal (Sri Villiputhur Nachiyar); its 11-tier gopuram is depicted on the Government of Tamil Nadu emblem — ~15–18 km by road from Krishnankovil.',
         'distance': '~15 km NW', 'rating': 4.9, 'timings': '6:00 AM – 12:30 PM & 4:00 PM – 9:00 PM', 'entry': 'Free'},
        {'name': 'Srivilliputhur Wildlife Sanctuary', 'type': 'Nature', 'emoji': '🐆',
         'description': 'Home to elephants, leopards, and rare birds in the Western Ghats foothills.',
         'distance': '18 km', 'rating': 4.3, 'timings': '6:00 AM – 6:00 PM', 'entry': '₹30'},
    ],
    'Srivilliputhur': [
        {'name': 'Andal Temple (Vatapatrasayi Temple)', 'type': 'Religious', 'emoji': '🛕',
         'description': 'One of the 108 Divya Desams; the temple gopuram is the emblem of Tamil Nadu.',
         'distance': '0 km', 'rating': 4.9, 'timings': '6:00 AM – 12:30 PM & 4:00 PM – 9:00 PM', 'entry': 'Free'},
        {'name': 'Srivilliputhur Wildlife Sanctuary', 'type': 'Nature', 'emoji': '🐆',
         'description': 'Dense forest in the Western Ghats hosting elephants, leopards, gaur and diverse birdlife.',
         'distance': '5 km', 'rating': 4.4, 'timings': '6:00 AM – 6:00 PM', 'entry': '₹30'},
        {'name': 'Pazhavoor Lakshmi Narasimhar Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Ancient Narasimha temple with intricate stone carvings.',
         'distance': '10 km', 'rating': 4.4, 'timings': '6:00 AM – 12:00 PM & 4:00 PM – 8:00 PM', 'entry': 'Free'},
    ],
    'Tenkasi': [
        {'name': 'Kasi Viswanathar Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Known as the "Kasi of the South" – a grand Shiva temple with a 180-foot gopuram.',
         'distance': '0 km', 'rating': 4.8, 'timings': '6:00 AM – 12:00 PM & 4:00 PM – 9:00 PM', 'entry': 'Free'},
        {'name': 'Courtallam Waterfalls', 'type': 'Nature', 'emoji': '💦',
         'description': 'Famous "Spa of South India" with 9 waterfalls, medicinal mineral waters.',
         'distance': '5 km', 'rating': 4.7, 'timings': '6:00 AM – 8:00 PM (best Jun–Sep)', 'entry': 'Free'},
        {'name': 'Agasthiyar Falls', 'type': 'Nature', 'emoji': '🌊',
         'description': 'Scenic waterfall in the Western Ghats near Papanasam dam.',
         'distance': '40 km', 'rating': 4.5, 'timings': '6:00 AM – 6:00 PM', 'entry': 'Free'},
    ],
    'Dindigul': [
        {'name': 'Dindigul Rock Fort', 'type': 'Heritage', 'emoji': '🏰',
         'description': 'Impressive fort on a 273-foot granite rock with panoramic valley views.',
         'distance': '0 km', 'rating': 4.5, 'timings': '9:00 AM – 5:30 PM', 'entry': '₹5'},
        {'name': 'Kodaikanal (Day Trip)', 'type': 'Hill Station', 'emoji': '⛰️',
         'description': 'Princess of Hill Stations – beautiful lake, meadows, and valleys at 2,100 m altitude.',
         'distance': '100 km', 'rating': 4.8, 'timings': 'All day', 'entry': 'Free'},
        {'name': 'Palani Murugan Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Famous hilltop Murugan temple, one of the six abodes of Murugan.',
         'distance': '65 km', 'rating': 4.9, 'timings': '6:00 AM – 9:00 PM', 'entry': 'Free'},
    ],
    'Salem': [
        {'name': 'Yercaud (Day Trip)', 'type': 'Hill Station', 'emoji': '⛰️',
         'description': 'Poor man\'s Ooty – beautiful lake, rose garden, and coffee plantations.',
         'distance': '32 km', 'rating': 4.6, 'timings': 'All day', 'entry': 'Free'},
        {'name': 'Salem Steel Plant Museum', 'type': 'Industrial', 'emoji': '🏭',
         'description': 'One of India\'s largest steel plants with a visitor museum.',
         'distance': '3 km', 'rating': 4.1, 'timings': '10:00 AM – 5:00 PM (Mon–Fri)', 'entry': 'Free'},
        {'name': 'Kottai (Salem Fort)', 'type': 'Heritage', 'emoji': '🏰',
         'description': 'Ancient fort with panoramic views of the city.',
         'distance': '2 km', 'rating': 4.0, 'timings': '6:00 AM – 6:00 PM', 'entry': 'Free'},
    ],
    'Rajapalayam': [
        {'name': 'Rajapalayam Murugan Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Important Murugan temple visited by thousands of devotees annually.',
         'distance': '0 km', 'rating': 4.5, 'timings': '6:00 AM – 9:00 PM', 'entry': 'Free'},
        {'name': 'Ariyanayagipuram Waterfall', 'type': 'Nature', 'emoji': '💦',
         'description': 'Scenic waterfall in the hills near Rajapalayam.',
         'distance': '15 km', 'rating': 4.2, 'timings': '6:00 AM – 6:00 PM', 'entry': 'Free'},
    ],
    'Tirumangalam': [
        {'name': 'Thiruparankundram Murugan Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'One of the six abodes of Murugan, rock-cut cave temple near Madurai.',
         'distance': '20 km', 'rating': 4.8, 'timings': '6:00 AM – 1:00 PM & 2:30 PM – 9:00 PM', 'entry': 'Free'},
        {'name': 'Kallazhagar Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Ancient Vishnu temple set in a scenic valley, one of the 108 Divya Desams.',
         'distance': '18 km', 'rating': 4.7, 'timings': '6:00 AM – 12:30 PM & 4:00 PM – 9:00 PM', 'entry': 'Free'},
    ],
    'Erode': [
        {'name': 'Bhavani Sangamam', 'type': 'Religious', 'emoji': '🌊',
         'description': 'Confluence of Bhavani and Kaveri rivers, sacred bathing ghat.',
         'distance': '15 km', 'rating': 4.4, 'timings': 'Open 24 hrs', 'entry': 'Free'},
        {'name': 'Kodiveri Dam', 'type': 'Nature', 'emoji': '🏞️',
         'description': 'Scenic dam across River Bhavani surrounded by hills, popular picnic spot.',
         'distance': '30 km', 'rating': 4.5, 'timings': '8:00 AM – 6:00 PM', 'entry': 'Free'},
    ],
    'Tiruppur': [
        {'name': 'Thirumoorthi Mountains', 'type': 'Nature', 'emoji': '⛰️',
         'description': 'Scenic mountain range with a Murugan temple at the top.',
         'distance': '40 km', 'rating': 4.4, 'timings': '6:00 AM – 6:00 PM', 'entry': 'Free'},
        {'name': 'Valparai (Day Trip)', 'type': 'Hill Station', 'emoji': '🌿',
         'description': 'Tea and coffee plantation hill station with beautiful wildlife.',
         'distance': '70 km', 'rating': 4.7, 'timings': 'All day', 'entry': 'Free'},
    ],
    'Sivakasi': [
        {'name': 'Meenakshi Amman Temple (nearby Madurai)', 'type': 'Religious', 'emoji': '🛕',
         'description': 'The world-famous Meenakshi temple is just 70 km away in Madurai.',
         'distance': '70 km', 'rating': 4.9, 'timings': '5:00 AM – 12:30 PM & 4:00 PM – 10:00 PM', 'entry': 'Free'},
        {'name': 'Arulmigu Subramaniya Swamy Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Important Murugan temple in Sivakasi with annual festivals.',
         'distance': '0 km', 'rating': 4.5, 'timings': '6:00 AM – 9:00 PM', 'entry': 'Free'},
    ],
    'Hosur': [
        {'name': 'Hosur Fort', 'type': 'Heritage', 'emoji': '🏰',
         'description': '18th-century fort with historic significance in Krishnagiri district.',
         'distance': '2 km', 'rating': 3.9, 'timings': '9:00 AM – 5:30 PM', 'entry': 'Free'},
        {'name': 'Rayakottai Fort', 'type': 'Heritage', 'emoji': '🏰',
         'description': 'Hilltop fort with panoramic views, historically significant.',
         'distance': '20 km', 'rating': 4.2, 'timings': '9:00 AM – 5:00 PM', 'entry': 'Free'},
        {'name': 'Bangalore (Bengaluru) Day Trip', 'type': 'City', 'emoji': '🌆',
         'description': 'Silicon Valley of India just 40 km away with Lalbagh, Palace and more.',
         'distance': '40 km', 'rating': 4.7, 'timings': 'All day', 'entry': 'Various'},
    ],
    'Trichy': [
        {'name': 'Sri Ranganathaswamy Temple, Srirangam', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Massive Vaishnavite temple on an island — among the largest temple complexes in India.',
         'distance': '8 km from city centre', 'rating': 4.9, 'timings': '6:00 AM – 9:00 PM', 'entry': 'Free'},
        {'name': 'Rockfort Ucchi Pillayar Temple', 'type': 'Religious', 'emoji': '⛰️',
         'description': 'Ancient rock-cut temple atop an 83 m rock with city views.',
         'distance': '1 km', 'rating': 4.7, 'timings': '6:00 AM – 8:00 PM', 'entry': '₹5'},
        {'name': 'St. Mary\'s Cathedral', 'type': 'Religious', 'emoji': '⛪',
         'description': 'Gothic-style Roman Catholic cathedral in the heart of Tiruchirappalli.',
         'distance': '2 km', 'rating': 4.4, 'timings': '6:00 AM – 8:00 PM', 'entry': 'Free'},
    ],
    'Virudhunagar': [
        {'name': 'Virudhunagar Town Heritage Walk', 'type': 'Local', 'emoji': '🚶',
         'description': 'Bustling market town — famous for savoury snacks (sevu, mixture) and temples.',
         'distance': '0 km', 'rating': 4.1, 'timings': 'Daytime', 'entry': 'Free'},
        {'name': 'Madurai Meenakshi Temple (day trip)', 'type': 'Religious', 'emoji': '🛕',
         'description': 'World-famous temple ~45 min by road toward Madurai.',
         'distance': '45 km', 'rating': 4.9, 'timings': '5:00 AM – 10:00 PM', 'entry': 'Free'},
    ],
    'Vellore': [
        {'name': 'Vellore Fort & Jalakandeswarar Temple', 'type': 'Heritage', 'emoji': '🏰',
         'description': '16th-century fort with a historic Shiva temple inside.',
         'distance': '0 km', 'rating': 4.5, 'timings': '9:00 AM – 5:00 PM', 'entry': '₹5'},
        {'name': 'Golden Temple (Sripuram)', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Stunning gold-covered temple of Mahalakshmi in Thirumalaikodi.',
         'distance': '8 km', 'rating': 4.8, 'timings': '4:00 AM – 8:00 PM', 'entry': 'Free'},
    ],
    'Watrap': [
        {'name': 'Watrap Lake & surrounds', 'type': 'Nature', 'emoji': '🌿',
         'description': 'Quiet town between Srivilliputhur and Rajapalayam — good stop for local snacks.',
         'distance': '0 km', 'rating': 4.0, 'timings': 'Daytime', 'entry': 'Free'},
        {'name': 'Srivilliputhur Andal Temple (short hop)', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Iconic gopuram — among the 108 Divya Desams.',
         'distance': '12 km', 'rating': 4.9, 'timings': '6:00 AM – 9:00 PM', 'entry': 'Free'},
    ],
    'Coimbatore': [
        {'name': 'Marudamalai Murugan Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Hilltop Murugan temple with views over the Western Ghats.',
         'distance': '12 km', 'rating': 4.7, 'timings': '6:00 AM – 8:30 PM', 'entry': 'Free'},
        {'name': 'Gedee Car Museum', 'type': 'Museum', 'emoji': '🚗',
         'description': 'Vintage and classic cars in a compact museum.',
         'distance': '5 km', 'rating': 4.3, 'timings': '9:00 AM – 6:00 PM', 'entry': '₹75'},
    ],
    'Palani': [
        {'name': 'Palani Dhandayuthapani Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Famous hill Murugan temple — one of the six Arupadai Veedu.',
         'distance': '0 km', 'rating': 4.9, 'timings': '6:00 AM – 9:00 PM', 'entry': 'Free'},
    ],
    'Krishnagiri': [
        {'name': 'Krishnagiri Fort', 'type': 'Heritage', 'emoji': '🏰',
         'description': 'Hill fort with views; gateway town between Bengaluru and Salem.',
         'distance': '3 km', 'rating': 4.0, 'timings': '9:00 AM – 5:00 PM', 'entry': 'Free'},
    ],
    'Sankarankoil': [
        {'name': 'Sankaranarayanar Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Ancient temple blending Shiva and Vishnu in one sanctum.',
         'distance': '0 km', 'rating': 4.6, 'timings': '6:00 AM – 9:00 PM', 'entry': 'Free'},
    ],
    'Thanjavur': [
        {'name': 'Brihadeeswarar Temple (Big Temple)', 'type': 'UNESCO Heritage', 'emoji': '🛕',
         'description': '11th-century Chola granite temple — one of the Great Living Chola Temples; towering vimana and Nandi mandapam.',
         'distance': '~2 km from Thanjavur Jn area', 'rating': 4.9, 'timings': '6:00 AM – 12:30 PM & 4:00 PM – 8:30 PM', 'entry': '₹50 (Indians, check current)'},
        {'name': 'Thanjavur Maratha Palace & Saraswati Mahal Library', 'type': 'Heritage', 'emoji': '🏛️',
         'description': 'Royal palace complex with art gallery and rare palm-leaf manuscripts.',
         'distance': '1 km', 'rating': 4.4, 'timings': '9:00 AM – 5:00 PM (closed Fri)', 'entry': '₹10–50'},
    ],
    'Alagapuri': [
        {'name': 'Village shrines & agrarian belt', 'type': 'Local', 'emoji': '🌾',
         'description': 'Small hamlet between Krishnankovil and Tirumangalam on many campus buses — quiet rural Tamil landscape; nearest major sights are Tirumangalam / Madurai.',
         'distance': '0 km', 'rating': 4.0, 'timings': 'Daytime', 'entry': 'Free'},
    ],
    'T. Kalupatti': [
        {'name': 'Local temples & weekly market', 'type': 'Local', 'emoji': '🛕',
         'description': 'Town en route to Tirumangalam; typical roadside temples and markets — combine with a stop in Tirumangalam or Madurai for major monuments.',
         'distance': '0 km', 'rating': 4.0, 'timings': 'Morning & evening', 'entry': 'Free'},
    ],
    'Sattur': [
        {'name': 'Sattur Kara Sev & local bazaar', 'type': 'Food & Culture', 'emoji': '🍿',
         'description': 'This Virudhunagar-district town is famous for savoury snacks (kara sev, mixture) and weekly markets — a classic highway stop between Virudhunagar and Madurai.',
         'distance': '0 km', 'rating': 4.2, 'timings': 'Daytime', 'entry': 'Free'},
        {'name': 'Madurai Meenakshi Temple (day trip)', 'type': 'Religious', 'emoji': '🛕',
         'description': 'World-famous Meenakshi-Sundareswarar temple is a short drive toward Madurai.',
         'distance': '~45 km', 'rating': 4.9, 'timings': '5:00 AM – 10:00 PM', 'entry': 'Free'},
    ],
    'Surandai': [
        {'name': 'Surandai town & Tenkasi road', 'type': 'Local', 'emoji': '🚶',
         'description': 'Busy junction between Tenkasi and Tirunelveli — good for local eats before Courtallam or the Nellai belt.',
         'distance': '0 km', 'rating': 4.0, 'timings': 'Daytime', 'entry': 'Free'},
        {'name': 'Courtallam waterfalls (short hop)', 'type': 'Nature', 'emoji': '💦',
         'description': 'Famous falls near Tenkasi — combine with a Tenkasi–Tirunelveli trip.',
         'distance': '~25–35 km', 'rating': 4.7, 'timings': 'Seasonal hours', 'entry': 'Free'},
    ],
    'Karur': [
        {'name': 'Pasupatheeswarar Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Ancient Shiva temple on the banks of the Amaravathi; Karur is a major Kongu Nadu junction on Madurai–Coimbatore routes.',
         'distance': 'Central town', 'rating': 4.5, 'timings': '6:00 AM – 12:00 PM & 4:00 PM – 8:30 PM', 'entry': 'Free'},
        {'name': 'Mayanur / Amaravathi river ghats', 'type': 'Nature', 'emoji': '🌊',
         'description': 'Scenic river stretches near Karur — popular for evening walks and local festivals.',
         'distance': '5–12 km', 'rating': 4.2, 'timings': 'Open', 'entry': 'Free'},
    ],
    'Aruppukottai': [
        {'name': 'Arulmigu Thandu Mariamman Temple', 'type': 'Religious', 'emoji': '🛕',
         'description': 'Important Amman shrine in the heart of Aruppukottai — the town is a busy hub between Virudhunagar and the coast.',
         'distance': '0 km', 'rating': 4.4, 'timings': '6:00 AM – 12:00 PM & 4:00 PM – 9:00 PM', 'entry': 'Free'},
        {'name': 'Virudhunagar & snack trail', 'type': 'Food & Culture', 'emoji': '🍘',
         'description': 'Pair with Virudhunagar’s famous savoury snacks (sevu, mixture) a short hop away.',
         'distance': '~15 km', 'rating': 4.2, 'timings': 'Daytime', 'entry': 'Free'},
    ],
}


TOURISM_PLACE_ALIASES = {
    'krishnankoil': 'Krishnankovil',
    'krishnan koil': 'Krishnankovil',
    'trichy': 'Trichy',
    'tiruchirappalli': 'Trichy',
    'dindugal': 'Dindigul',
    'srivilliputtur': 'Srivilliputhur',
    'virudhunagar': 'Virudhunagar',
    'watrap': 'Watrap',
    'vellore': 'Vellore',
    'coimbatore': 'Coimbatore',
    'palani': 'Palani',
    'krishnagiri': 'Krishnagiri',
    'sankarankoil': 'Sankarankoil',
    't. kalupatti': 'T. Kalupatti',
    't kalupatti': 'T. Kalupatti',
    'alagapuri': 'Alagapuri',
    'thanjavur': 'Thanjavur',
    'tanjore': 'Thanjavur',
    'sattur': 'Sattur',
    'surandai': 'Surandai',
    'karur': 'Karur',
    'aruppukottai': 'Aruppukottai',
}


def _coerce_via_stops_list(vs):
    """Turn LocalBus.via_stops into a list of strings (JSON array, or plain text with ; / ,)."""
    if vs is None:
        return []
    if isinstance(vs, (list, tuple)):
        out = []
        for x in vs:
            if x is None:
                continue
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    if isinstance(vs, str):
        s = vs.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if x is not None and str(x).strip()]
            if isinstance(parsed, str):
                s = parsed.strip()
        except Exception:
            pass
        merged = s.replace(',', ';')
        return [p.strip() for p in merged.split(';') if p.strip()]
    return []


def _sort_tourism_spots(spots):
    """Higher rating first (more acclaimed / verified in our guide)."""
    if not spots:
        return []

    def _rk(s):
        try:
            return -float(s.get('rating') or 0)
        except (TypeError, ValueError):
            return 0.0

    return sorted(spots, key=_rk)


def _dedupe_tourism_spots(spots):
    """Avoid listing the same attraction name twice in one column."""
    seen, out = set(), []
    for s in spots or []:
        n = (s.get('name') or '').strip().lower()
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(s)
    return out


def normalize_tourism_place(name):
    if not name:
        return ''
    s = str(name).strip()
    if not s:
        return ''
    return TOURISM_PLACE_ALIASES.get(s.lower(), s.title())


def get_tourism_spots(destination):
    """Return tourism spots for a city name (with common spelling aliases)."""
    if not destination:
        return []
    d = normalize_tourism_place(destination)
    if d in TOURISM_SPOTS:
        return TOURISM_SPOTS[d]
    raw = destination.strip()
    if raw in TOURISM_SPOTS:
        return TOURISM_SPOTS[raw]
    # Title-case fallback for keys that match normalize_tourism_place output
    t = raw.title()
    return TOURISM_SPOTS.get(t, [])


def tourism_bundle_for_timetables(origin, destination, local_buses_list):
    """
    Spots at origin & destination plus famous places at each via stop on returned govt buses.
    Sorted by rating, de-duplicated, with more entries per column for richer detail.
    """
    o = normalize_tourism_place(origin)
    d = normalize_tourism_place(destination)
    origin_spots = _sort_tourism_spots(_dedupe_tourism_spots(get_tourism_spots(o) or []))[:8]
    dest_spots = _sort_tourism_spots(_dedupe_tourism_spots(get_tourism_spots(d) or []))[:8]
    bundle = {
        'origin_name': o,
        'destination_name': d,
        'origin_spots': origin_spots,
        'destination_spots': dest_spots,
        'via_segments': [],
    }
    seen = {x for x in (o, d) if x}
    ordered = []
    for bus in local_buses_list or []:
        vs = _coerce_via_stops_list(getattr(bus, 'via_stops', None))
        for stop in vs:
            pl = normalize_tourism_place(stop)
            if not pl or pl in seen:
                continue
            seen.add(pl)
            ordered.append(pl)
    for pl in ordered:
        raw = get_tourism_spots(pl)
        if not raw:
            continue
        spots = _sort_tourism_spots(_dedupe_tourism_spots(raw))[:6]
        if spots:
            bundle['via_segments'].append({'place': pl, 'spots': spots})
    return bundle


# ─────────────────────────────────────────────────────────────────────────────
# New Passenger Routes
# ─────────────────────────────────────────────────────────────────────────────

PLACES_LIST = [
    'Krishnankovil', 'Sivakasi', 'Srivilliputhur', 'Tirumangalam', 'Tenkasi', 'Rajapalayam',
    'Madurai', 'Dindigul', 'Salem', 'Erode', 'Tiruppur', 'Hosur', 'Chennai', 'Bengaluru'
]

# Live GPS map on timetables for these pairs (order-independent keys).
# Includes Krishnankovil ↔ Madurai/Rajapalayam so campus searches still get the live row + green LED.
GPS_ENABLED_ROUTE_KEYS = frozenset({
    'madurai|rajapalayam',
    'krishnankovil|madurai',
    'krishnankovil|rajapalayam',
})


def _gps_route_key(origin, destination):
    """Order-independent key, e.g. madurai|rajapalayam."""
    a = (origin or '').strip().lower()
    b = (destination or '').strip().lower()
    if not a or not b:
        return ''
    return '|'.join(sorted([a, b]))


def _gps_live_stale_seconds():
    """
    Max age of last DB fix to still count as 'live' in JSON.
    Default 120s: after USB/bridge stops, the site stops showing green within ~2 minutes.
    Override: env GPS_LIVE_STALE_SECONDS (15–86400).
    """
    try:
        v = int(os.environ.get('GPS_LIVE_STALE_SECONDS', '120'))
        return max(15, min(86400, v))
    except (TypeError, ValueError):
        return 120


@app.route('/weather')
def weather_page():
    city = request.args.get('city', 'Madurai').strip()
    weather = get_weather_data(city)
    forecast = get_weather_forecast(city)
    return render_template('passengers/weather.html',
                           weather=weather, forecast=forecast,
                           city=city, cities=PLACES_LIST)


@app.route('/api/weather')
def api_weather():
    """Return weather + travel suggestions for a city as JSON."""
    city = request.args.get('city', '').strip()
    if not city:
        return jsonify({'error': 'city required'}), 400

    # Normalise city spelling
    _ALIASES = {
        'krishnankoil': 'Krishnankovil', 'krishnan koil': 'Krishnankovil',
        'srivilliputtur': 'Srivilliputhur', 'bangalore': 'Bengaluru',
        'trivandrum': 'Thiruvananthapuram', 'bombay': 'Mumbai',
        'calcutta': 'Kolkata', 'madras': 'Chennai',
    }
    city_norm = _ALIASES.get(city.lower(), city.title())

    weather = get_weather_data(city_norm)
    if not weather:
        # Try original casing
        weather = get_weather_data(city)

    if not weather:
        return jsonify({'status': 'unavailable', 'city': city_norm, 'weather': None, 'suggestions': []})

    suggestions = get_weather_recommendations(weather, city_norm)

    # Extra travel-mode suggestions
    condition = (weather.get('condition') or '').lower()
    temp      = weather.get('temperature', 30)
    travel_tips = []

    if condition in ['rain', 'drizzle', 'rainy', 'thunderstorm']:
        travel_tips.append({'icon': '&#128665;', 'color': '#1565C0', 'text': 'Prefer train or bus over bike in wet weather.'})
        travel_tips.append({'icon': '&#9749;',   'color': '#E65100', 'text': 'Roads may be slippery — allow extra travel time.'})
    elif condition == 'thunderstorm':
        travel_tips.append({'icon': '&#9889;',   'color': '#B71C1C', 'text': 'Avoid road travel if possible. Take train.'})
    elif temp > 38:
        travel_tips.append({'icon': '&#9728;',   'color': '#E65100', 'text': 'Very hot! Prefer AC bus or train. Carry water.'})
    elif temp < 15:
        travel_tips.append({'icon': '&#129398;', 'color': '#1565C0', 'text': 'Cool weather — good for road travel.'})
    else:
        travel_tips.append({'icon': '&#9989;',   'color': '#2E7D32', 'text': 'Good weather for travel. All modes available.'})

    cond_icon = {
        'clear': '&#9728;', 'clouds': '&#9925;', 'rain': '&#127783;',
        'drizzle': '&#127783;', 'thunderstorm': '&#9928;', 'snow': '&#10052;',
        'mist': '&#127787;', 'haze': '&#127787;', 'fog': '&#127787;',
    }.get(condition, '&#127782;')

    _ALIASE_DESC = {
        'sunny': 'Clear skies',
        'cloudy': 'Overcast',
        'rainy': 'Rain',
        'foggy': 'Fog or mist',
        'partly_cloudy': 'Partly cloudy',
    }
    description = weather.get('description') or weather.get('forecast') or _ALIASE_DESC.get(
        condition, (weather.get('condition') or 'Weather').replace('_', ' ').title()
    )

    return jsonify({
        'status':      'ok',
        'city':        city_norm,
        'weather': {
            'temperature': weather.get('temperature'),
            'feels_like':  weather.get('feels_like'),
            'description': description,
            'humidity':    weather.get('humidity'),
            'wind_speed':  weather.get('wind_speed'),
            'condition':   condition,
            'icon':        cond_icon,
        },
        'suggestions': [s.get('message','') for s in suggestions],
        'travel_tips': travel_tips,
    })


@app.route('/api/gps/update', methods=['POST', 'OPTIONS'])
@csrf.exempt
def api_gps_update():
    """
    Ingest GPS from on-board device (ESP32 / Arduino + WiFi).
    JSON body: api_key, latitude, longitude, speed_kmh (optional), bus_number,
               route_origin, route_destination, device_id (optional).
    Set env GPS_API_KEY on the server; same value in firmware.
    """
    if request.method == 'OPTIONS':
        return '', 204

    secret = os.environ.get('GPS_API_KEY', 'yatra-gps-dev-key-change-me')
    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        data = {}

    key = data.get('api_key') or request.headers.get('X-GPS-Api-Key') or request.args.get('api_key')
    if not key or key != secret:
        return jsonify({'status': 'error', 'message': 'invalid or missing api_key'}), 401

    try:
        lat = float(data.get('latitude'))
        lng = float(data.get('longitude'))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'latitude and longitude required (numbers)'}), 400

    ro = (data.get('route_origin') or '').strip()
    rd = (data.get('route_destination') or '').strip()
    rk = _gps_route_key(ro, rd)
    if rk not in GPS_ENABLED_ROUTE_KEYS:
        return jsonify({
            'status': 'error',
            'message': 'route not enabled for GPS. Use Rajapalayam + Madurai.',
            'route_key': rk or None,
        }), 400

    speed = data.get('speed_kmh')
    try:
        speed = float(speed) if speed is not None else 0.0
    except (TypeError, ValueError):
        speed = 0.0

    bus_number = (data.get('bus_number') or 'TN55L 9003').strip()[:40]
    device_id = (data.get('device_id') or 'bus1').strip()[:64]

    heading = data.get('heading')
    try:
        heading = float(heading) if heading is not None else None
    except (TypeError, ValueError):
        heading = None

    now = datetime.utcnow()
    row = BusGPSTelemetry.query.filter_by(route_key=rk, device_id=device_id).first()
    if row:
        row.latitude = lat
        row.longitude = lng
        row.speed_kmh = speed
        row.bus_number = bus_number
        row.heading = heading
        row.last_updated = now
    else:
        row = BusGPSTelemetry(
            route_key=rk,
            device_id=device_id,
            bus_number=bus_number,
            latitude=lat,
            longitude=lng,
            speed_kmh=speed,
            heading=heading,
            last_updated=now,
        )
        db.session.add(row)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.exception('gps commit: %s', e)
        return jsonify({'status': 'error', 'message': 'database error'}), 500

    return jsonify({'status': 'ok', 'route_key': rk, 'bus_number': bus_number, 'last_updated': now.isoformat() + 'Z'})


@app.route('/api/gps/live', methods=['GET', 'OPTIONS'])
def api_gps_live():
    """Public: live bus positions only when origin/destination match an enabled GPS route."""
    if request.method == 'OPTIONS':
        return '', 204

    origin = request.args.get('origin', '').strip()
    destination = request.args.get('destination', '').strip()
    _ALI = {
        'krishnankoil': 'Krishnankovil', 'krishnan koil': 'Krishnankovil',
        'srivilliputtur': 'Srivilliputhur', 'bangalore': 'Bengaluru',
    }

    def _nn(c):
        if not c:
            return ''
        return _ALI.get(c.lower().strip(), c.strip().title())

    o = _nn(origin)
    d = _nn(destination)
    rk = _gps_route_key(o, d)
    if rk not in GPS_ENABLED_ROUTE_KEYS:
        return jsonify({
            'status': 'ok',
            'tracked': False,
            'message': 'Live GPS is only available for Rajapalayam ↔ Madurai.',
            'buses': [],
        })

    rows = BusGPSTelemetry.query.filter_by(route_key=rk).all()
    stale_sec = _gps_live_stale_seconds()
    buses = [r.to_live_dict(stale_seconds=stale_sec) for r in rows]
    return jsonify({
        'status': 'ok',
        'tracked': True,
        'route_key': rk,
        'route_label': 'Rajapalayam - Madurai',
        'live_stale_after_seconds': stale_sec,
        'buses': buses,
    })


@app.route('/train-map')
def train_map():
    origin = request.args.get('origin', '').strip()
    destination = request.args.get('destination', '').strip()
    return render_template('passengers/train_map.html',
                           origin=origin, destination=destination,
                           places=PLACES_LIST)


@app.route('/tourism')
def tourism_page():
    destination = request.args.get('destination', '').strip()
    spots = get_tourism_spots(destination)
    return render_template('passengers/tourism.html',
                           destination=destination,
                           spots=spots,
                           places=PLACES_LIST)


@app.route('/api/seed-bus-data', methods=['POST'])
@login_required
def seed_bus_data():
    """Seed LocalBus and PrivateOperator tables with real-world Tamil Nadu bus data."""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403

    if LocalBus.query.count() > 10:
        return jsonify({'message': 'Bus data already seeded', 'count': LocalBus.query.count()})

    from datetime import time as _time

    local_bus_data = [
        # Srivilliputhur ↔ Krishnankovil
        {'bus_number': 'TN59 3001', 'route_number': '59A', 'operator': 'TNSTC',
         'origin': 'Srivilliputhur', 'destination': 'Krishnankovil',
         'departure_time': _time(6, 0), 'arrival_time': _time(6, 45),
         'via_stops': ['Watrap', 'Rajapalayam'], 'fare': 25, 'bus_type': 'Express', 'status': 'scheduled'},
        {'bus_number': 'TN59 3002', 'route_number': '59A', 'operator': 'TNSTC',
         'origin': 'Srivilliputhur', 'destination': 'Krishnankovil',
         'departure_time': _time(9, 30), 'arrival_time': _time(10, 15),
         'via_stops': ['Watrap'], 'fare': 25, 'bus_type': 'Ordinary', 'status': 'scheduled'},
        {'bus_number': 'TN59 3005', 'route_number': '59B', 'operator': 'TNSTC',
         'origin': 'Krishnankovil', 'destination': 'Srivilliputhur',
         'departure_time': _time(7, 30), 'arrival_time': _time(8, 15),
         'via_stops': ['Watrap'], 'fare': 25, 'bus_type': 'Express', 'status': 'scheduled'},
        # Srivilliputhur ↔ Madurai
        {'bus_number': 'TN59 1101', 'route_number': '101', 'operator': 'SETC',
         'origin': 'Srivilliputhur', 'destination': 'Madurai',
         'departure_time': _time(5, 30), 'arrival_time': _time(8, 30),
         'via_stops': ['Rajapalayam', 'Virudhunagar'], 'fare': 80, 'bus_type': 'Superfast', 'status': 'scheduled'},
        {'bus_number': 'TN59 1102', 'route_number': '101', 'operator': 'SETC',
         'origin': 'Srivilliputhur', 'destination': 'Madurai',
         'departure_time': _time(8, 0), 'arrival_time': _time(11, 0),
         'via_stops': ['Rajapalayam', 'Virudhunagar', 'Tirumangalam'], 'fare': 75, 'bus_type': 'Express', 'status': 'scheduled'},
        {'bus_number': 'TN59 1103', 'route_number': '101', 'operator': 'SETC',
         'origin': 'Srivilliputhur', 'destination': 'Madurai',
         'departure_time': _time(14, 0), 'arrival_time': _time(17, 0),
         'via_stops': ['Rajapalayam', 'Virudhunagar'], 'fare': 80, 'bus_type': 'Superfast', 'status': 'scheduled'},
        {'bus_number': 'TN59 1110', 'route_number': '101R', 'operator': 'SETC',
         'origin': 'Madurai', 'destination': 'Srivilliputhur',
         'departure_time': _time(6, 30), 'arrival_time': _time(9, 30),
         'via_stops': ['Virudhunagar', 'Rajapalayam'], 'fare': 80, 'bus_type': 'Superfast', 'status': 'scheduled'},
        {'bus_number': 'TN59 1111', 'route_number': '101R', 'operator': 'SETC',
         'origin': 'Madurai', 'destination': 'Srivilliputhur',
         'departure_time': _time(16, 0), 'arrival_time': _time(19, 0),
         'via_stops': ['Virudhunagar', 'Rajapalayam'], 'fare': 80, 'bus_type': 'Express', 'status': 'scheduled'},
        # Madurai ↔ Chennai
        {'bus_number': 'TN59 2001', 'route_number': '201', 'operator': 'SETC',
         'origin': 'Madurai', 'destination': 'Chennai',
         'departure_time': _time(7, 0), 'arrival_time': _time(15, 30),
         'via_stops': ['Dindigul', 'Salem', 'Vellore'], 'fare': 350, 'bus_type': 'Deluxe', 'status': 'scheduled'},
        {'bus_number': 'TN59 2002', 'route_number': '201', 'operator': 'SETC',
         'origin': 'Madurai', 'destination': 'Chennai',
         'departure_time': _time(22, 0), 'arrival_time': _time(6, 0),
         'via_stops': ['Dindigul', 'Trichy', 'Salem'], 'fare': 320, 'bus_type': 'Express', 'status': 'scheduled'},
        {'bus_number': 'TN59 2010', 'route_number': '201R', 'operator': 'SETC',
         'origin': 'Chennai', 'destination': 'Madurai',
         'departure_time': _time(21, 30), 'arrival_time': _time(6, 0),
         'via_stops': ['Vellore', 'Salem', 'Dindigul'], 'fare': 350, 'bus_type': 'Deluxe', 'status': 'scheduled'},
        # Madurai ↔ Bengaluru
        {'bus_number': 'TN59 3101', 'route_number': '301', 'operator': 'SETC',
         'origin': 'Madurai', 'destination': 'Bengaluru',
         'departure_time': _time(19, 0), 'arrival_time': _time(5, 30),
         'via_stops': ['Dindigul', 'Salem', 'Hosur'], 'fare': 420, 'bus_type': 'Superfast', 'status': 'scheduled'},
        {'bus_number': 'TN59 3110', 'route_number': '301R', 'operator': 'SETC',
         'origin': 'Bengaluru', 'destination': 'Madurai',
         'departure_time': _time(20, 0), 'arrival_time': _time(7, 0),
         'via_stops': ['Hosur', 'Salem', 'Dindigul'], 'fare': 420, 'bus_type': 'Superfast', 'status': 'scheduled'},
        # Tenkasi ↔ Madurai
        {'bus_number': 'TN59 4001', 'route_number': '401', 'operator': 'TNSTC',
         'origin': 'Tenkasi', 'destination': 'Madurai',
         'departure_time': _time(6, 0), 'arrival_time': _time(9, 30),
         'via_stops': ['Tirunelveli', 'Sankarankoil'], 'fare': 110, 'bus_type': 'Express', 'status': 'scheduled'},
        {'bus_number': 'TN59 4002', 'route_number': '401', 'operator': 'TNSTC',
         'origin': 'Tenkasi', 'destination': 'Madurai',
         'departure_time': _time(12, 0), 'arrival_time': _time(15, 30),
         'via_stops': ['Sankarankoil', 'Virudhunagar'], 'fare': 110, 'bus_type': 'Ordinary', 'status': 'scheduled'},
        # Dindigul ↔ Salem
        {'bus_number': 'TN29 5001', 'route_number': '501', 'operator': 'TNSTC',
         'origin': 'Dindigul', 'destination': 'Salem',
         'departure_time': _time(7, 0), 'arrival_time': _time(11, 0),
         'via_stops': ['Natham', 'Namakkal'], 'fare': 130, 'bus_type': 'Express', 'status': 'scheduled'},
        # Erode ↔ Chennai
        {'bus_number': 'TN33 6001', 'route_number': '601', 'operator': 'SETC',
         'origin': 'Erode', 'destination': 'Chennai',
         'departure_time': _time(22, 0), 'arrival_time': _time(6, 30),
         'via_stops': ['Salem', 'Vellore'], 'fare': 280, 'bus_type': 'Deluxe', 'status': 'scheduled'},
        # Tiruppur ↔ Chennai
        {'bus_number': 'TN39 7001', 'route_number': '701', 'operator': 'SETC',
         'origin': 'Tiruppur', 'destination': 'Chennai',
         'departure_time': _time(21, 30), 'arrival_time': _time(6, 0),
         'via_stops': ['Erode', 'Salem', 'Vellore'], 'fare': 300, 'bus_type': 'Superfast', 'status': 'scheduled'},
        # Krishnankovil ↔ Madurai
        {'bus_number': 'TN59 8001', 'route_number': '801', 'operator': 'TNSTC',
         'origin': 'Krishnankovil', 'destination': 'Madurai',
         'departure_time': _time(6, 30), 'arrival_time': _time(9, 0),
         'via_stops': ['Virudhunagar', 'Tirumangalam'], 'fare': 70, 'bus_type': 'Express', 'status': 'scheduled'},
        {'bus_number': 'TN59 8002', 'route_number': '801', 'operator': 'TNSTC',
         'origin': 'Krishnankovil', 'destination': 'Madurai',
         'departure_time': _time(11, 0), 'arrival_time': _time(13, 30),
         'via_stops': ['Virudhunagar'], 'fare': 65, 'bus_type': 'Ordinary', 'status': 'scheduled'},
        {'bus_number': 'TN59 8010', 'route_number': '801R', 'operator': 'TNSTC',
         'origin': 'Madurai', 'destination': 'Krishnankovil',
         'departure_time': _time(15, 0), 'arrival_time': _time(17, 30),
         'via_stops': ['Tirumangalam', 'Virudhunagar'], 'fare': 70, 'bus_type': 'Express', 'status': 'scheduled'},
    ]

    private_bus_data = [
        # Srivilliputhur ↔ Chennai
        {'operator_name': 'KPN Travels', 'bus_number': 'TN59 P001', 'route_name': 'Srivilliputhur – Chennai',
         'origin': 'Srivilliputhur', 'destination': 'Chennai',
         'departure_time': _time(20, 0), 'arrival_time': _time(6, 30),
         'via_stops': ['Virudhunagar', 'Madurai', 'Trichy', 'Salem'],
         'fare': 650, 'bus_type': 'AC Sleeper', 'rating': 4.2, 'live_tracking': True,
         'duration': '10h 30m', 'status': 'available'},
        {'operator_name': 'SRS Travels', 'bus_number': 'TN59 P002', 'route_name': 'Srivilliputhur – Chennai',
         'origin': 'Srivilliputhur', 'destination': 'Chennai',
         'departure_time': _time(21, 30), 'arrival_time': _time(7, 0),
         'via_stops': ['Virudhunagar', 'Madurai', 'Salem'],
         'fare': 550, 'bus_type': 'Non-AC Sleeper', 'rating': 3.9, 'live_tracking': True,
         'duration': '9h 30m', 'status': 'available'},
        # Srivilliputhur ↔ Bengaluru
        {'operator_name': 'VRL Travels', 'bus_number': 'KA01 P001', 'route_name': 'Srivilliputhur – Bengaluru',
         'origin': 'Srivilliputhur', 'destination': 'Bengaluru',
         'departure_time': _time(19, 30), 'arrival_time': _time(6, 0),
         'via_stops': ['Virudhunagar', 'Madurai', 'Dindigul', 'Salem', 'Hosur'],
         'fare': 750, 'bus_type': 'AC Sleeper', 'rating': 4.4, 'live_tracking': True,
         'duration': '10h 30m', 'status': 'available'},
        # Madurai ↔ Chennai
        {'operator_name': 'KPN Travels', 'bus_number': 'TN58 P101', 'route_name': 'Madurai – Chennai',
         'origin': 'Madurai', 'destination': 'Chennai',
         'departure_time': _time(21, 0), 'arrival_time': _time(6, 0),
         'via_stops': ['Dindigul', 'Trichy', 'Salem', 'Vellore'],
         'fare': 700, 'bus_type': 'AC Sleeper', 'rating': 4.5, 'live_tracking': True,
         'duration': '9h', 'status': 'available'},
        {'operator_name': 'Parveen Travels', 'bus_number': 'TN58 P102', 'route_name': 'Madurai – Chennai',
         'origin': 'Madurai', 'destination': 'Chennai',
         'departure_time': _time(22, 30), 'arrival_time': _time(6, 30),
         'via_stops': ['Dindigul', 'Salem'],
         'fare': 480, 'bus_type': 'Non-AC Sleeper', 'rating': 4.0, 'live_tracking': True,
         'duration': '8h', 'status': 'available'},
        # Madurai ↔ Bengaluru
        {'operator_name': 'KSRTC', 'bus_number': 'KA01 P201', 'route_name': 'Madurai – Bengaluru',
         'origin': 'Madurai', 'destination': 'Bengaluru',
         'departure_time': _time(18, 0), 'arrival_time': _time(5, 0),
         'via_stops': ['Dindigul', 'Salem', 'Hosur'],
         'fare': 850, 'bus_type': 'AC Volvo', 'rating': 4.6, 'live_tracking': True,
         'duration': '11h', 'status': 'available'},
        {'operator_name': 'SRM Travels', 'bus_number': 'TN58 P202', 'route_name': 'Madurai – Bengaluru',
         'origin': 'Madurai', 'destination': 'Bengaluru',
         'departure_time': _time(20, 30), 'arrival_time': _time(7, 30),
         'via_stops': ['Dindigul', 'Hosur'],
         'fare': 650, 'bus_type': 'AC Seater', 'rating': 4.1, 'live_tracking': True,
         'duration': '11h', 'status': 'available'},
        # Chennai ↔ Bengaluru
        {'operator_name': 'VRL Travels', 'bus_number': 'KA01 P301', 'route_name': 'Chennai – Bengaluru',
         'origin': 'Chennai', 'destination': 'Bengaluru',
         'departure_time': _time(22, 0), 'arrival_time': _time(7, 0),
         'via_stops': ['Vellore', 'Krishnagiri'],
         'fare': 900, 'bus_type': 'AC Sleeper', 'rating': 4.6, 'live_tracking': True,
         'duration': '9h', 'status': 'available'},
        {'operator_name': 'Orange Travels', 'bus_number': 'KA01 P302', 'route_name': 'Chennai – Bengaluru',
         'origin': 'Chennai', 'destination': 'Bengaluru',
         'departure_time': _time(23, 0), 'arrival_time': _time(7, 30),
         'via_stops': ['Hosur'],
         'fare': 650, 'bus_type': 'Non-AC Sleeper', 'rating': 3.8, 'live_tracking': True,
         'duration': '8h 30m', 'status': 'available'},
    ]

    added_local = 0
    for b in local_bus_data:
        exists = LocalBus.query.filter_by(bus_number=b['bus_number']).first()
        if not exists:
            lb = LocalBus(
                bus_number=b['bus_number'], route_number=b['route_number'],
                operator=b['operator'], origin=b['origin'], destination=b['destination'],
                departure_time=b['departure_time'], arrival_time=b['arrival_time'],
                via_stops=b.get('via_stops', []), fare=b.get('fare', 0),
                bus_type=b.get('bus_type', 'Ordinary'), status=b.get('status', 'scheduled'),
                seat_availability=random.randint(10, 45), total_seats=50, is_active=True
            )
            db.session.add(lb)
            added_local += 1

    added_private = 0
    for b in private_bus_data:
        exists = PrivateOperator.query.filter_by(bus_number=b['bus_number']).first()
        if not exists:
            pb = PrivateOperator(
                operator_name=b['operator_name'], bus_number=b['bus_number'],
                route_name=b['route_name'], origin=b['origin'], destination=b['destination'],
                departure_time=b['departure_time'], arrival_time=b['arrival_time'],
                via_stops=b.get('via_stops', []), fare=b['fare'],
                bus_type=b.get('bus_type', 'AC Seater'), rating=b.get('rating', 4.0),
                live_tracking=b.get('live_tracking', True), duration=b.get('duration', ''),
                status=b.get('status', 'available'),
                seat_availability=random.randint(5, 35), total_seats=40, is_active=True
            )
            db.session.add(pb)
            added_private += 1

    db.session.commit()
    return jsonify({'message': f'Seeded {added_local} local buses and {added_private} private operators'})


@app.route('/api/travel-guide/route', methods=['POST'])
def api_travel_guide_route():
    """API endpoint for travel route planning"""
    data = request.json
    origin = data.get('origin', '').strip()
    destination = data.get('destination', '').strip()
    
    if not all([origin, destination]):
        return jsonify({'error': 'Origin and destination required'}), 400
    
    route_data = plan_travel_route(origin, destination)
    weather = get_weather_data(destination.split(',')[-1].strip() if ',' in destination else destination, date.today())
    
    route_coords = [route_data['origin_coordinates'], route_data['destination_coordinates']]
    for step in route_data['steps']:
        if 'coordinates' in step:
            route_coords.append((step['coordinates'][0], step['coordinates'][1]))
    
    landmarks = get_landmarks_near_route(route_coords)
    
    return jsonify({
        **route_data,
        'weather': weather,
        'landmarks': landmarks,
        'safety_tips': generate_safety_tips(route_data, weather)
    })

@app.route('/admin/init-educational-institutions', methods=['POST'])
@login_required
def init_educational_institutions():
    """Initialize educational institutions"""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    institutions_data = [
        {
            'name': 'Kalasalingam University',
            'institution_type': 'University',
            'location': 'Krishnankovil, Srivilliputhur',
            'address': 'Anand Nagar, Krishnankovil, Tamil Nadu',
            'latitude': 9.7072,
            'longitude': 77.5076,
            'nearest_bus_stop': 'Krishnankovil Bus Stop',
            'distance_to_bus_stop_km': 0.8
        },
        {
            'name': 'MEPCO Schlenk Engineering College',
            'institution_type': 'College',
            'location': 'Sivakasi',
            'address': 'MEPCO Nagar, Sivakasi, Tamil Nadu',
            'latitude': 9.4600,
            'longitude': 77.8100,
            'nearest_bus_stop': 'MEPCO Nagar Bus Stop',
            'distance_to_bus_stop_km': 0.5
        },
        {
            'name': 'PSR Engineering College',
            'institution_type': 'College',
            'location': 'Sivakasi',
            'address': 'PSR Nagar, Sankarankovil Road, Sivakasi, Tamil Nadu',
            'latitude': 9.4500,
            'longitude': 77.7900,
            'nearest_bus_stop': 'PSR Nagar Bus Stop',
            'distance_to_bus_stop_km': 0.3
        }
    ]
    
    for inst_data in institutions_data:
        existing = EducationalInstitution.query.filter_by(name=inst_data['name']).first()
        if not existing:
            institution = EducationalInstitution(**inst_data)
            db.session.add(institution)
    
    db.session.commit()
    flash('Educational institutions initialized successfully!', 'success')
    return redirect(url_for('travel_guide'))

@app.route('/admin/init-landmarks', methods=['POST'])
@login_required
def init_landmarks():
    """Initialize landmarks and rest points"""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    landmarks_data = [
        {'name': 'Krishnankovil Tea Stall', 'landmark_type': 'tea_stall', 'location': 'Krishnankovil', 'lat': 9.572774, 'lng': 77.678473, 'is_rest_point': True},
        {'name': 'Srivilliputhur ATM', 'landmark_type': 'atm', 'location': 'Srivilliputhur', 'lat': 9.5121, 'lng': 77.6336, 'is_rest_point': False},
        {'name': 'Srivilliputhur Petrol Bunk', 'landmark_type': 'petrol_bunk', 'location': 'Srivilliputhur', 'lat': 9.5150, 'lng': 77.6350, 'is_rest_point': False},
        {'name': 'Tiruthangal Tea Stall', 'landmark_type': 'tea_stall', 'location': 'Tiruthangal', 'lat': 9.4833, 'lng': 77.8167, 'is_rest_point': True},
        {'name': 'Sivakasi Bus Stand Tea Stall', 'landmark_type': 'tea_stall', 'location': 'Sivakasi', 'lat': 9.4492, 'lng': 77.7974, 'is_rest_point': True},
        {'name': 'Sivakasi ATM', 'landmark_type': 'atm', 'location': 'Sivakasi', 'lat': 9.4500, 'lng': 77.8000, 'is_rest_point': False},
        {'name': 'MEPCO Road Tea Stall', 'landmark_type': 'tea_stall', 'location': 'Sivakasi', 'lat': 9.4550, 'lng': 77.8050, 'is_rest_point': True},
        {'name': 'PSR Road Petrol Bunk', 'landmark_type': 'petrol_bunk', 'location': 'Sivakasi', 'lat': 9.4480, 'lng': 77.7920, 'is_rest_point': False}
    ]
    
    for landmark_data in landmarks_data:
        existing = Landmark.query.filter_by(
            name=landmark_data['name'],
            location=landmark_data['location']
        ).first()
        if not existing:
            landmark = Landmark(
                name=landmark_data['name'],
                landmark_type=landmark_data['landmark_type'],
                location=landmark_data['location'],
                latitude=landmark_data['lat'],
                longitude=landmark_data['lng'],
                is_rest_point=landmark_data['is_rest_point']
            )
            db.session.add(landmark)
    
    db.session.commit()
    flash('Landmarks initialized successfully!', 'success')
    return redirect(url_for('travel_guide'))

@app.route('/admin/update-db-schema', methods=['POST'])
@login_required
def update_db_schema():
    """Update database schema to add missing columns"""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        from sqlalchemy import text
        
        # Check and add live_tracking column to private_operator table
        try:
            db.session.execute(text("ALTER TABLE private_operator ADD COLUMN live_tracking BOOLEAN DEFAULT 1"))
            db.session.commit()
            flash('Added live_tracking column', 'success')
        except Exception as e:
            if 'duplicate column' not in str(e).lower() and 'already exists' not in str(e).lower():
                app.logger.warning(f"Error adding live_tracking: {e}")
            db.session.rollback()
        
        # Check and add duration column to private_operator table
        try:
            db.session.execute(text("ALTER TABLE private_operator ADD COLUMN duration VARCHAR(20)"))
            db.session.commit()
            flash('Added duration column', 'success')
        except Exception as e:
            if 'duplicate column' not in str(e).lower() and 'already exists' not in str(e).lower():
                app.logger.warning(f"Error adding duration: {e}")
            db.session.rollback()
        
        return redirect(url_for('multi_modal_coordination'))
    except Exception as e:
        flash(f'Error updating schema: {str(e)}', 'danger')
        return redirect(url_for('multi_modal_coordination'))

# ─────────────────────────────────────────────────────────────────────────────
# Airport & Flight Data
# ─────────────────────────────────────────────────────────────────────────────

# Major Indian airports with coordinates
INDIAN_AIRPORTS = [
    {'code':'MAA','name':'Chennai International Airport',        'city':'Chennai',         'lat':12.9941,'lng':80.1709,'terminal':'T1/T2'},
    {'code':'IXM','name':'Madurai Airport',                      'city':'Madurai',         'lat':9.8345, 'lng':78.0934,'terminal':'Main'},
    {'code':'CJB','name':'Coimbatore International Airport',     'city':'Coimbatore',      'lat':11.0300,'lng':77.0434,'terminal':'Main'},
    {'code':'BLR','name':'Kempegowda International Airport',     'city':'Bengaluru',       'lat':13.1979,'lng':77.7063,'terminal':'T1/T2'},
    {'code':'TRZ','name':'Tiruchirappalli International Airport','city':'Trichy',           'lat':10.7654,'lng':78.7097,'terminal':'Main'},
    {'code':'TIR','name':'Tirunelveli Airport',                  'city':'Tirunelveli',     'lat':8.7302, 'lng':77.6236,'terminal':'Main'},
    {'code':'TCR','name':'Tuticorin Airport',                    'city':'Thoothukudi',     'lat':8.7241, 'lng':78.0258,'terminal':'Main'},
    {'code':'SXV','name':'Salem Airport',                        'city':'Salem',           'lat':11.7833,'lng':78.0656,'terminal':'Main'},
    {'code':'HYD','name':'Rajiv Gandhi International Airport',   'city':'Hyderabad',       'lat':17.2403,'lng':78.4294,'terminal':'Main'},
    {'code':'COK','name':'Cochin International Airport',         'city':'Kochi',           'lat':10.1520,'lng':76.4019,'terminal':'Main'},
    {'code':'TRV','name':'Trivandrum International Airport',     'city':'Thiruvananthapuram','lat':8.4824,'lng':76.9201,'terminal':'Main'},
    {'code':'CCJ','name':'Calicut International Airport',        'city':'Kozhikode',       'lat':11.1368,'lng':75.9553,'terminal':'Main'},
    {'code':'BOM','name':'Chhatrapati Shivaji Maharaj International','city':'Mumbai',      'lat':19.0896,'lng':72.8656,'terminal':'T1/T2'},
    {'code':'DEL','name':'Indira Gandhi International Airport',  'city':'Delhi',           'lat':28.5562,'lng':77.1000,'terminal':'T1/T2/T3'},
    {'code':'PNQ','name':'Pune Airport',                         'city':'Pune',            'lat':18.5822,'lng':73.9197,'terminal':'Main'},
    {'code':'GOI','name':'Goa International Airport (Mopa)',     'city':'Goa',             'lat':15.3808,'lng':73.8314,'terminal':'Main'},
    {'code':'CCU','name':'Netaji Subhas Chandra Bose Airport',   'city':'Kolkata',         'lat':22.6547,'lng':88.4467,'terminal':'Main'},
    {'code':'AMD','name':'Sardar Vallabhbhai Patel International','city':'Ahmedabad',      'lat':23.0771,'lng':72.6347,'terminal':'Main'},
    {'code':'VTZ','name':'Visakhapatnam Airport',                'city':'Visakhapatnam',   'lat':17.7212,'lng':83.2246,'terminal':'Main'},
    {'code':'BBI','name':'Biju Patnaik International Airport',   'city':'Bhubaneswar',     'lat':20.2444,'lng':85.8178,'terminal':'Main'},
    {'code':'IXE','name':'Mangalore International Airport',      'city':'Mangalore',       'lat':12.9613,'lng':74.8904,'terminal':'Main'},
    {'code':'IXC','name':'Chandigarh Airport',                   'city':'Chandigarh',      'lat':30.6735,'lng':76.7885,'terminal':'Main'},
    {'code':'JAI','name':'Jaipur International Airport',         'city':'Jaipur',          'lat':26.8242,'lng':75.8122,'terminal':'Main'},
    {'code':'LKO','name':'Chaudhary Charan Singh Airport',       'city':'Lucknow',         'lat':26.7606,'lng':80.8893,'terminal':'Main'},
]

# City name → nearest IATA code
CITY_TO_IATA = {
    'chennai':'MAA','madras':'MAA',
    'madurai':'IXM',
    'coimbatore':'CJB','kovai':'CJB',
    'bengaluru':'BLR','bangalore':'BLR',
    'trichy':'TRZ','tiruchirappalli':'TRZ','tiruchirapalli':'TRZ',
    'tirunelveli':'TIR',
    'thoothukudi':'TCR','tuticorin':'TCR',
    'salem':'SXV',
    'hyderabad':'HYD',
    'kochi':'COK','cochin':'COK','ernakulam':'COK',
    'thiruvananthapuram':'TRV','trivandrum':'TRV',
    'kozhikode':'CCJ','calicut':'CCJ',
    'mumbai':'BOM','bombay':'BOM',
    'delhi':'DEL','new delhi':'DEL',
    'pune':'PNQ',
    'goa':'GOI','panaji':'GOI',
    'kolkata':'CCU','calcutta':'CCU',
    'ahmedabad':'AMD',
    'visakhapatnam':'VTZ','vizag':'VTZ',
    'bhubaneswar':'BBI',
    'mangalore':'IXE','mangaluru':'IXE',
    'chandigarh':'IXC',
    'jaipur':'JAI',
    'lucknow':'LKO',
}

# City coordinates for nearby-airport search
CITY_COORDS = {
    # Krishnankovil (KARE / town area — aligned with local GPS fixes)
    'krishnankovil':(9.572774,77.678473),'krishnankoil':(9.572774,77.678473),
    'krishnan kovil':(9.572774,77.678473),'krishnan koil':(9.572774,77.678473),
    'krishnankoil ': (9.572774,77.678473),
    'srivilliputhur':(9.5121,77.6336),'srivilliputtur':(9.5121,77.6336),
    'sivakasi':(9.4514,77.8086),'virudhunagar':(9.5849,77.9559),
    'rajapalayam':(9.4529,77.5568),'tenkasi':(8.9593,77.3152),
    'madurai':(9.9252,78.1198),'dindigul':(10.3624,77.9695),
    'trichy':(10.7905,78.7047),'tiruchirappalli':(10.7905,78.7047),
    'thanjavur':(10.7867,79.1378),'tirunelveli':(8.7139,77.7567),
    'thoothukudi':(8.7642,78.1348),'tuticorin':(8.7642,78.1348),
    'coimbatore':(11.0168,76.9558),'erode':(11.3410,77.7172),
    'tiruppur':(11.1085,77.3411),'salem':(11.6643,78.1460),
    'ooty':(11.4102,76.6950),'yercaud':(11.7784,78.2080),
    'chennai':(13.0827,80.2707),'vellore':(12.9165,79.1325),
    'bengaluru':(12.9716,77.5946),'bangalore':(12.9716,77.5946),
    'hosur':(12.7409,77.8253),'krishnagiri':(12.5186,78.2137),
    'mysuru':(12.2958,76.6394),'mysore':(12.2958,76.6394),
    'mangalore':(12.9141,74.8560),'mangaluru':(12.9141,74.8560),
    'hyderabad':(17.3850,78.4867),'secunderabad':(17.4358,78.5013),
    'kochi':(9.9312,76.2673),'ernakulam':(10.0097,76.2970),
    'mumbai':(19.0760,72.8777),'delhi':(28.6139,77.2090),
    'kolkata':(22.5726,88.3639),'howrah':(22.5839,88.3424),
    'pune':(18.5204,73.8567),'nagpur':(21.1458,79.0882),
    'nashik':(19.9975,73.7898),'aurangabad':(19.8762,75.3433),
    'ahmedabad':(23.0225,72.5714),'surat':(21.1702,72.8311),
    'vadodara':(22.3072,73.1812),'rajkot':(22.3039,70.8022),
    'visakhapatnam':(17.6868,83.2185),'vizag':(17.6868,83.2185),
    'vijayawada':(16.5193,80.6167),'tirupati':(13.6288,79.4192),
    'nellore':(14.4426,79.9865),'guntur':(16.3067,80.4365),
    'thiruvananthapuram':(8.5241,76.9366),'trivandrum':(8.5241,76.9366),
    'kozhikode':(11.2588,75.7804),'calicut':(11.2588,75.7804),
    'thrissur':(10.5276,76.2144),'palakkad':(10.7867,76.6548),
    'kollam':(8.8932,76.6141),'alappuzha':(9.4981,76.3388),
    'kottayam':(9.5916,76.5222),'nagercoil':(8.1693,77.4167),
    'jaipur':(26.9124,75.7873),'jodhpur':(26.2389,73.0243),
    'udaipur':(24.5854,73.7125),'ajmer':(26.4499,74.6399),
    'lucknow':(26.8467,80.9462),'kanpur':(26.4499,80.3319),
    'varanasi':(25.3176,82.9739),'allahabad':(25.4358,81.8463),
    'patna':(25.5941,85.1376),'guwahati':(26.1445,91.7362),
    'bhubaneswar':(20.2961,85.8245),'cuttack':(20.4625,85.8828),
    'agra':(27.1767,78.0081),'chandigarh':(30.7333,76.7794),
    'amritsar':(31.6340,74.8723),'ludhiana':(30.9010,75.8573),
    'bhopal':(23.2599,77.4126),'indore':(22.7196,75.8577),
    'goa':(15.2993,74.1240),'panaji':(15.4909,73.8278),
    'mysuru':(12.2958,76.6394),'mysore':(12.2958,76.6394),
    'hubli':(15.3647,75.1240),'dharwad':(15.4589,75.0078),
    'mangalore':(12.9141,74.8560),'mangaluru':(12.9141,74.8560),
    'tirumangalam':(9.8267,77.9878),
    # Virudhunagar district villages
    'watrap':(9.5418,77.7061),
    'tiruchuli':(9.6204,77.9741),'aruppukkottai':(9.5075,78.0964),
    'kariapatti':(9.4725,77.9108),'sattur':(9.3547,77.9098),
    'narikudi':(9.4008,78.0131),'vembakottai':(9.3614,77.8219),
    'ilanji':(9.4217,77.7731),'tiruttangal':(9.4231,77.8639),
    # Tirunelveli / Tenkasi
    'tenkasi':(8.9593,77.3152),'courtallam':(8.9354,77.2753),
    'ambasamudram':(8.7008,77.4521),'valliyur':(8.3865,77.6132),
    'nanguneri':(8.4975,77.6666),'tiruchendur':(8.4979,78.1243),
    'kayalpatnam':(8.5726,78.1257),'eral':(8.6276,77.8932),
    'ottapidaram':(8.7499,78.0609),'kadayanallur':(9.0829,77.3480),
    'surandai':(8.9808,77.4239),'alangulam':(8.8702,77.4143),
    'vikramasingapuram':(8.7973,77.4384),'cheranmahadevi':(8.7213,77.5274),
    'kalakkad':(8.4720,77.5513),'mulapozhi':(8.3533,77.5476),
    # Theni / Dindigul
    'theni':(10.0107,77.4770),'cumbum':(9.7314,77.2843),
    'periyakulam':(10.1194,77.5404),'uthamapalayam':(9.8061,77.3353),
    'bodinayakanur':(10.0110,77.3543),'gudalur':(11.4989,76.4965),
    'palani':(10.4510,77.5210),'batlagundu':(10.1597,77.7530),
    'natham':(10.3200,77.8850),'oddanchatram':(10.5610,77.7374),
    'vedasandur':(10.5340,77.9580),'kambam':(9.7288,77.2687),
    'shanarpatti':(10.1900,77.8500),'ayyampettai':(10.8753,79.1000),
    'sedapatti':(9.8900,78.0500),
    # Madurai district villages
    'melur':(10.0427,78.3347),'usilampatti':(9.9644,77.7028),
    'andipatti':(10.0219,77.6272),'sholavandan':(9.8489,78.0028),
    'vadipatti':(10.0769,77.9878),'nilakottai':(10.1689,77.8628),
    'kalligudi':(9.9156,78.0528),'peraiyur':(9.8444,77.9481),
    'kottampatti':(10.0222,77.8200),'alanganallur':(9.9989,78.1789),
    'sholavandan':(9.8489,78.0028),
    # Trichy / Salem / Coimbatore region
    'musiri':(10.9522,78.4426),'lalgudi':(10.8748,78.8174),
    'kulithalai':(10.9345,78.4241),'paramathi':(11.3860,78.1197),
    'rasipuram':(11.4568,78.1783),'tiruchengode':(11.3860,77.8945),
    'mettur':(11.7961,77.8005),'omalur':(11.7374,78.0441),
    'attur':(11.5964,78.5978),'pollachi':(10.6584,77.0085),
    'udumalaipettai':(10.5862,77.2513),'valparai':(10.3264,76.9618),
    'anaimalai':(10.5808,76.9281),'kinathukadavu':(10.6408,77.0947),
    'kovilpatti':(9.1765,77.8697),'rajapalayam':(9.4529,77.5568),
    'sivakasi':(9.4514,77.8086),'virudhunagar':(9.5849,77.9559),
}


import math as _math

def _haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = _math.radians(lat2 - lat1)
    dlon = _math.radians(lon2 - lon1)
    a = _math.sin(dlat/2)**2 + _math.cos(_math.radians(lat1)) * _math.cos(_math.radians(lat2)) * _math.sin(dlon/2)**2
    return R * 2 * _math.asin(_math.sqrt(a))


def _city_coords(city_name):
    """Return (lat, lng) for a city name via exact or partial match in CITY_COORDS."""
    c = city_name.strip().lower()
    # Normalize common alternate spellings
    _ALIASES = {
        'krishnankoil': 'krishnankovil',
        'krishnan koil': 'krishnankovil',
        'krishnan kovil': 'krishnankovil',
        'srivilliputtur': 'srivilliputhur',
        'bangalore': 'bengaluru',
        'trivandrum': 'thiruvananthapuram',
        'bombay': 'mumbai',
        'calcutta': 'kolkata',
        'madras': 'chennai',
    }
    c = _ALIASES.get(c, c)
    if c in CITY_COORDS:
        return CITY_COORDS[c]
    for k, v in CITY_COORDS.items():
        if c in k or k in c:
            return v
    return None


def _nearest_airport(city_name):
    """
    Find the nearest airport to a city.
    Returns (airport_dict, distance_km) or (None, None).
    """
    coords = _city_coords(city_name)
    if not coords:
        return None, None
    lat, lng = coords
    best, best_dist = None, float('inf')
    for ap in INDIAN_AIRPORTS:
        d = _haversine(lat, lng, ap['lat'], ap['lng'])
        if d < best_dist:
            best_dist = d
            best = ap
    return (best, round(best_dist, 1)) if best else (None, None)


def _nearest_station(city_name):
    """
    Find the nearest railway station to a city.
    Returns (station_code, station_info_tuple, distance_km) or (None, None, None).
    """
    coords = _city_coords(city_name)
    if not coords:
        return None, None, None
    lat, lng = coords
    best_code, best_info, best_dist = None, None, float('inf')
    for code, info in STATION_INFO.items():
        d = _haversine(lat, lng, info[2], info[3])
        if d < best_dist:
            best_dist = d
            best_code = code
            best_info = info
    return (best_code, best_info, round(best_dist, 1)) if best_code else (None, None, None)


@app.route('/api/nearby-airports')
def api_nearby_airports():
    """Return airports within radius_km of given lat/lng OR of a named city."""
    city = request.args.get('city', '').strip().lower()
    try:
        lat = float(request.args.get('lat', 0))
        lng = float(request.args.get('lng', 0))
    except ValueError:
        lat, lng = 0.0, 0.0

    if city and city in CITY_COORDS:
        lat, lng = CITY_COORDS[city]
    elif city:
        # Try partial match
        for k, v in CITY_COORDS.items():
            if city in k or k in city:
                lat, lng = v
                break

    if lat == 0 and lng == 0:
        return jsonify({'error': 'Provide lat/lng or a known city name', 'airports': []})

    try:
        radius_km = float(request.args.get('radius', 80))
    except ValueError:
        radius_km = 80

    nearby = []
    for ap in INDIAN_AIRPORTS:
        dist = _haversine(lat, lng, ap['lat'], ap['lng'])
        if dist <= radius_km:
            nearby.append({**ap, 'distance_km': round(dist, 1)})
    nearby.sort(key=lambda x: x['distance_km'])
    return jsonify({'airports': nearby, 'count': len(nearby), 'searched_lat': lat, 'searched_lng': lng})


@app.route('/api/campus-gpt/query', methods=['GET', 'POST', 'OPTIONS'])
def campus_gpt_query():
    """
    Unified Campus GPT integration endpoint.
    Accepts a natural-language query and returns structured transport data.

    GET  /api/campus-gpt/query?q=Check+timings+for+Krishnankovil+to+Madurai
    POST /api/campus-gpt/query  body: {"query": "next bus from Krishnankovil to Madurai"}
    """
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        resp = jsonify({'ok': True})
        resp.headers['Access-Control-Allow-Origin']  = '*'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
        resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
        return resp

    import re as _re

    # ── Extract query string ──────────────────────────────────────────────────
    if request.method == 'POST':
        body  = request.get_json(silent=True) or {}
        query = body.get('query', '').strip()
    else:
        query = request.args.get('q', request.args.get('query', '')).strip()

    if not query:
        return jsonify({'error': 'Provide a query via ?q= or POST body {"query":"..."}'}), 400

    q = query.lower()

    # ── Simple intent + entity extractor ─────────────────────────────────────
    # Spelling aliases (same list as elsewhere in the app)
    _ALIASES = {
        'krishnankoil': 'Krishnankovil', 'krishnan koil': 'Krishnankovil',
        'krishnan kovil': 'Krishnankovil', 'srivilliputtur': 'Srivilliputhur',
        'bangalore': 'Bengaluru', 'trivandrum': 'Thiruvananthapuram',
        'bombay': 'Mumbai', 'calcutta': 'Kolkata', 'madras': 'Chennai',
        'kovil': 'Krishnankovil', 'svpt': 'Srivilliputhur',
        'vdn': 'Virudhunagar', 'mdu': 'Madurai', 'cbe': 'Coimbatore',
    }

    def _normalise(word):
        return _ALIASES.get(word.lower(), word.title())

    # Detect mode
    mode = 'bus'
    if any(w in q for w in ['train', 'irctc', 'railway', 'express', 'superfast']):
        mode = 'train'
    elif any(w in q for w in ['flight', 'fly', 'airport', 'plane', 'air']):
        mode = 'flight'
    elif any(w in q for w in ['car', 'drive', 'bike', 'road', 'distance', 'km', 'travel time']):
        mode = 'road'

    # Extract origin and destination using common patterns
    origin, destination = None, None
    # Words to strip before treating as city names
    _STOP = {'check','timings','timing','schedule','bus','train','flight',
             'for','the','a','an','any','next','last','when','how','what',
             'is','are','there','show','get','find','me','please','can','i'}

    def _clean_city(raw):
        words = raw.strip().split()
        words = [w for w in words if w.lower() not in _STOP]
        return _normalise(' '.join(words)) if words else ''

    # Pattern 1: "from X to Y"
    m = _re.search(r'from\s+([a-z ]+?)\s+to\s+([a-z ]+?)(?:\s+by|\s+bus|\s+train|\s+via|$)', q)
    if m:
        origin      = _clean_city(m.group(1))
        destination = _clean_city(m.group(2))
    if not origin or not destination:
        # Pattern 2: "for X to Y"
        m = _re.search(r'for\s+([a-z ]+?)\s+to\s+([a-z ]+?)(?:\s+by|\s+bus|\s+train|\s*$)', q)
        if m:
            origin      = _clean_city(m.group(1))
            destination = _clean_city(m.group(2))
    if not origin or not destination:
        # Pattern 3: bare "X to Y" — exclude common noise words as left token
        m2 = _re.search(r'\b([a-z]{4,}(?:\s[a-z]{3,})?)\s+to\s+([a-z]{4,}(?:\s[a-z]{3,})?)\b', q)
        if m2:
            cand1 = _clean_city(m2.group(1))
            cand2 = _clean_city(m2.group(2))
            if cand1 and cand2:
                origin, destination = cand1, cand2

    if not origin or not destination:
        return jsonify({
            'intent': 'unknown',
            'answer': (
                "I could not understand the origin and destination from your query. "
                "Please try: 'Check bus timings from Krishnankovil to Madurai'"
            ),
            'data': {}
        })

    # ── Route intent detection ────────────────────────────────────────────────
    intent = 'bus_timings'
    if mode == 'train':
        intent = 'train_timings'
    elif mode == 'flight':
        intent = 'flight_info'
    elif mode == 'road':
        intent = 'road_distance'
    elif any(w in q for w in ['next bus', 'when is', 'next', 'timing', 'time', 'schedule', 'depart']):
        intent = 'bus_timings'
    elif any(w in q for w in ['seat', 'available', 'crowd', 'full', 'empty']):
        intent = 'seat_availability'
    elif any(w in q for w in ['fare', 'cost', 'price', 'how much', 'ticket']):
        intent = 'fare_info'

    # ── Fetch data ────────────────────────────────────────────────────────────
    ol, dl = origin.lower(), destination.lower()
    response_data = {}
    answer_lines  = []

    if intent in ('bus_timings', 'seat_availability', 'fare_info'):
        # Query both govt and private buses
        govt_buses = LocalBus.query.filter(
            LocalBus.is_active == True,
            or_(
                and_(func.lower(LocalBus.origin) == ol, func.lower(LocalBus.destination) == dl),
                and_(func.lower(LocalBus.origin) == dl, func.lower(LocalBus.destination) == ol)
            )
        ).order_by(LocalBus.departure_time).all()

        pvt_buses = PrivateOperator.query.filter(
            PrivateOperator.is_active == True,
            or_(
                and_(func.lower(PrivateOperator.origin) == ol, func.lower(PrivateOperator.destination) == dl),
                and_(func.lower(PrivateOperator.origin) == dl, func.lower(PrivateOperator.destination) == ol)
            )
        ).order_by(PrivateOperator.departure_time).all()

        total = len(govt_buses) + len(pvt_buses)

        if total == 0:
            answer_lines.append(
                f"Sorry, no bus services found between {origin} and {destination}. "
                "Try searching on the Yatra Saarthi website for more options."
            )
        else:
            if intent == 'fare_info':
                answer_lines.append(f"**Fare information — {origin} to {destination}:**")
                for b in govt_buses[:5]:
                    answer_lines.append(f"• {b.operator} ({b.bus_type}): ₹{b.fare:.0f}")
                for b in pvt_buses[:5]:
                    answer_lines.append(f"• {b.operator_name} ({b.bus_type}): ₹{b.fare:.0f}")

            elif intent == 'seat_availability':
                answer_lines.append(f"**Seat availability — {origin} to {destination}:**")
                for b in (govt_buses + pvt_buses)[:6]:
                    name = getattr(b, 'operator', None) or getattr(b, 'operator_name', '')
                    dep  = b.departure_time.strftime('%H:%M') if b.departure_time else '--:--'
                    seats = b.seat_availability
                    status = "🟢 Available" if seats > 10 else ("🟡 Filling fast" if seats > 0 else "🔴 Full")
                    answer_lines.append(f"• {dep} | {name} | {seats} seats left {status}")

            else:  # bus_timings (default)
                answer_lines.append(f"**Buses from {origin} to {destination}:**")
                if govt_buses:
                    answer_lines.append(f"\n🚌 Government Buses ({len(govt_buses)} found):")
                    for b in govt_buses[:6]:
                        dep = b.departure_time.strftime('%H:%M') if b.departure_time else '--:--'
                        arr = b.arrival_time.strftime('%H:%M')   if b.arrival_time  else '--:--'
                        answer_lines.append(
                            f"  • {dep} → {arr} | {b.operator} | {b.bus_type} | ₹{b.fare:.0f} | {b.seat_availability} seats"
                        )
                if pvt_buses:
                    answer_lines.append(f"\n🚌 Private Buses ({len(pvt_buses)} found):")
                    for b in pvt_buses[:6]:
                        dep = b.departure_time.strftime('%H:%M') if b.departure_time else '--:--'
                        arr = b.arrival_time.strftime('%H:%M')   if b.arrival_time  else '--:--'
                        answer_lines.append(
                            f"  • {dep} → {arr} | {b.operator_name} | {b.bus_type} | ₹{b.fare:.0f} | Rating: {b.rating}⭐"
                        )

        response_data = {
            'govt_buses':    [b.to_dict() for b in govt_buses],
            'private_buses': [{'operator': b.operator_name, 'bus_number': b.bus_number,
                               'route': b.route_name, 'departure': b.departure_time.strftime('%H:%M'),
                               'arrival': b.arrival_time.strftime('%H:%M'), 'fare': b.fare,
                               'bus_type': b.bus_type, 'seats': b.seat_availability,
                               'rating': b.rating, 'duration': b.duration}
                              for b in pvt_buses],
            'total_count': total,
        }

    elif intent == 'road_distance':
        # Inline Haversine road estimate
        import math as _m
        def _road(o, d):
            _SP = {'krishnankoil':'krishnankovil','bangalore':'bengaluru',
                   'trivandrum':'thiruvananthapuram','bombay':'mumbai','calcutta':'kolkata'}
            c1 = CITY_COORDS.get(_SP.get(o, o))
            c2 = CITY_COORDS.get(_SP.get(d, d))
            if not c1 or not c2:
                return None
            dlat = _m.radians(c2[0]-c1[0]); dlon = _m.radians(c2[1]-c1[1])
            a = _m.sin(dlat/2)**2 + _m.cos(_m.radians(c1[0]))*_m.cos(_m.radians(c2[0]))*_m.sin(dlon/2)**2
            km = round(6371*2*_m.asin(_m.sqrt(a))*1.3)
            return km
        dist = _road(ol, dl)
        if dist:
            car_h, car_m  = divmod(int(dist/60*60), 60)
            bike_h, bike_m = divmod(int(dist/40*60), 60)
            car_fuel  = round(dist/15*102)
            bike_fuel = round(dist/45*102)
            answer_lines += [
                f"**Road distance — {origin} to {destination}:**",
                f"📍 Distance: ~{dist} km (approximate)",
                f"🚗 By Car:  ~{car_h}h {car_m}m  |  Fuel ~₹{car_fuel}",
                f"🏍️ By Bike: ~{bike_h}h {bike_m}m  |  Fuel ~₹{bike_fuel}",
            ]
            response_data = {'distance_km': dist, 'car_hours': car_h, 'car_mins': car_m,
                             'bike_hours': bike_h, 'bike_mins': bike_m,
                             'car_fuel': car_fuel, 'bike_fuel': bike_fuel}
        else:
            answer_lines.append(f"Could not calculate road distance for {origin} to {destination}.")

    else:
        answer_lines.append(
            f"For {mode} information between {origin} and {destination}, "
            f"please visit the Yatra Saarthi website or use the search page."
        )

    base = request.host_url.rstrip('/')
    resp = jsonify({
        'intent':      intent,
        'mode':        mode,
        'origin':      origin,
        'destination': destination,
        'query':       query,
        'answer':      '\n'.join(answer_lines),
        'data':        response_data,
        'yatraSaarthiUrl': f'{base}/passengers/timetables?origin={origin}&destination={destination}',
    })
    resp.headers['Access-Control-Allow-Origin']  = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    return resp


@app.route('/api/nearby-stations')
def api_nearby_stations():
    """Return railway stations within radius_km of a city name or lat/lng."""
    city = request.args.get('city', '').strip()
    try:
        lat = float(request.args.get('lat', 0))
        lng = float(request.args.get('lng', 0))
    except ValueError:
        lat, lng = 0.0, 0.0

    coords = _city_coords(city) if city else None
    if coords:
        lat, lng = coords

    if lat == 0 and lng == 0:
        return jsonify({'error': 'Provide lat/lng or a known city name', 'stations': []})

    try:
        radius_km = float(request.args.get('radius', 80))
    except ValueError:
        radius_km = 80

    nearby = []
    for code, info in STATION_INFO.items():
        dist = _haversine(lat, lng, info[2], info[3])
        if dist <= radius_km:
            nearby.append({
                'code':     code,
                'name':     info[0],
                'city':     info[1],
                'lat':      info[2],
                'lng':      info[3],
                'distance_km': round(dist, 1),
            })
    nearby.sort(key=lambda x: x['distance_km'])
    return jsonify({'stations': nearby, 'count': len(nearby), 'searched_lat': lat, 'searched_lng': lng})


@app.route('/api/live-flights')
def api_live_flights():
    """
    Fetch live flight data via AviationStack API.
    Requires AVIATIONSTACK_KEY environment variable.
    Falls back to nearby airport info if no key is set.
    """
    origin_city = request.args.get('origin', '').strip()
    dest_city   = request.args.get('destination', '').strip()
    origin_lower = origin_city.lower()
    dest_lower   = dest_city.lower()

    dep_iata = CITY_TO_IATA.get(origin_lower)
    arr_iata = CITY_TO_IATA.get(dest_lower)

    dep_nearest_used = False
    arr_nearest_used = False
    dep_nearest_dist = None
    arr_nearest_dist = None

    # Fallback: find nearest airport if city not directly mapped
    dep_airport = next((a for a in INDIAN_AIRPORTS if a['code'] == dep_iata), None)
    if not dep_iata or not dep_airport:
        near_ap, near_dist = _nearest_airport(origin_city)
        if near_ap:
            dep_iata    = near_ap['code']
            dep_airport = near_ap
            dep_nearest_used = True
            dep_nearest_dist = near_dist

    arr_airport = next((a for a in INDIAN_AIRPORTS if a['code'] == arr_iata), None)
    if not arr_iata or not arr_airport:
        near_ap, near_dist = _nearest_airport(dest_city)
        if near_ap:
            arr_iata    = near_ap['code']
            arr_airport = near_ap
            arr_nearest_used = True
            arr_nearest_dist = near_dist

    if requests is None:
        return jsonify({'status': 'no_key', 'message': 'requests library not installed. Run: pip install requests',
                        'dep_iata': dep_iata, 'arr_iata': arr_iata,
                        'dep_airport': dep_airport, 'arr_airport': arr_airport, 'flights': [],
                        'setup_url': 'https://aviationstack.com/signup/free'})

    api_key = os.getenv('AVIATIONSTACK_KEY', 'd6d59dcf080219b7bc215d8e0da6f6e4').strip()

    if not api_key:
        return jsonify({
            'status': 'no_key',
            'message': 'Live data unavailable — AVIATIONSTACK_KEY not configured.',
            'dep_iata': dep_iata, 'arr_iata': arr_iata,
            'dep_airport': dep_airport, 'arr_airport': arr_airport,
            'flights': [], 'setup_url': 'https://aviationstack.com/signup/free',
        })

    if not dep_iata or not arr_iata:
        return jsonify({
            'status': 'no_airport',
            'message': f'Could not find any airport near {"origin" if not dep_iata else "destination"} city.',
            'dep_iata': dep_iata, 'arr_iata': arr_iata,
            'dep_airport': dep_airport, 'arr_airport': arr_airport,
            'flights': [],
        })

    try:
        url = (f"http://api.aviationstack.com/v1/flights"
               f"?access_key={api_key}&dep_iata={dep_iata}&arr_iata={arr_iata}&limit=50")
        resp = requests.get(url, timeout=10)
        data = resp.json()
        raw = data.get('data', [])
        flights = []
        for f in raw:
            dep = f.get('departure', {})
            arr = f.get('arrival', {})
            flights.append({
                'flight_number': f.get('flight', {}).get('iata', ''),
                'airline':       f.get('airline', {}).get('name', ''),
                'status':        f.get('flight_status', 'unknown').title(),
                'dep_airport':   dep.get('airport', dep_iata),
                'arr_airport':   arr.get('airport', arr_iata),
                'dep_scheduled': dep.get('scheduled', ''),
                'arr_scheduled': arr.get('scheduled', ''),
                'dep_actual':    dep.get('actual', ''),
                'arr_actual':    arr.get('actual', ''),
                'dep_delay':     dep.get('delay', 0) or 0,
                'arr_delay':     arr.get('delay', 0) or 0,
                'terminal_dep':  dep.get('terminal', ''),
                'gate_dep':      dep.get('gate', ''),
            })
        return jsonify({
            'status': 'ok',
            'dep_iata': dep_iata, 'arr_iata': arr_iata,
            'dep_airport': dep_airport, 'arr_airport': arr_airport,
            'flights': flights, 'count': len(flights),
            'dep_nearest_used': dep_nearest_used, 'dep_nearest_dist': dep_nearest_dist,
            'arr_nearest_used': arr_nearest_used, 'arr_nearest_dist': arr_nearest_dist,
            'origin_city': origin_city, 'dest_city': dest_city,
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'flights': []})


# ─────────────────────────────────────────────────────────────────────────────
# IRCTC / Train Station Data
# ─────────────────────────────────────────────────────────────────────────────

# City name → primary station code (IRCTC station codes)
CITY_TO_STATION = {
    # Tamil Nadu
    'srivilliputhur': 'SVPR', 'srivilliputtur': 'SVPR',
    # krishnankovil / krishnankoil — no direct station, geo-nearest will pick VPT/SVPR
    'sivakasi':       'SVKS',   'virudhunagar':  'VPT',
    'rajapalayam':    'RJPM',   'tenkasi':       'TNKV',
    'madurai':        'MDU',     'dindigul':      'DG',
    'trichy':         'TPJ',     'tiruchirappalli':'TPJ',
    'thanjavur':      'TJ',      'tirunelveli':   'TEN',
    'nagercoil':      'NCJ',     'thoothukudi':   'TN',
    'tuticorin':      'TN',      'coimbatore':    'CBE',
    'tiruppur':       'TUP',     'erode':         'ED',
    'salem':          'SA',      'ooty':          'UAM',
    'chennai':        'MAS',     'chennai central':'MAS',
    'chennai egmore': 'MS',      'vellore':       'VLR',
    'katpadi':        'KPD',     'chengalpattu':  'CGL',
    'tambaram':       'TBM',     'kumbakonam':    'KMU',
    'mayiladuthurai': 'MV',      'nagapattinam':  'NGT',
    'cuddalore':      'CUPJ',    'villupuram':    'VM',
    'hosur':          'HSRA',    'krishnagiri':   'KRR',
    # Karnataka
    'bengaluru':      'SBC',     'bangalore':     'SBC',
    'bangalore city': 'SBC',     'bangalore cant':'BNC',
    'mysuru':         'MYS',     'mysore':        'MYS',
    'mangalore':      'MAQ',     'mangaluru':     'MAQ',
    'hubli':          'UBL',     'dharwad':       'DWR',
    # Andhra / Telangana
    'hyderabad':      'SC',      'secunderabad':  'SC',
    'vijayawada':     'BZA',     'visakhapatnam': 'VSKP',
    'vizag':          'VSKP',    'tirupati':      'TPTY',
    'nellore':        'NLR',     'guntur':        'GNT',
    # Kerala
    'kochi':          'ERS',     'cochin':        'ERS',
    'ernakulam':      'ERN',     'thiruvananthapuram':'TVC',
    'trivandrum':     'TVC',     'kozhikode':     'CLT',
    'calicut':        'CLT',     'thrissur':      'TCR',
    'palakkad':       'PGT',     'kollam':        'QLN',
    'alappuzha':      'ALLP',    'kottayam':      'KTYM',
    # Maharashtra
    'mumbai':         'CSMT',    'bombay':        'CSMT',
    'pune':           'PUNE',    'nagpur':        'NGP',
    'nashik':         'NK',      'aurangabad':    'AWB',
    # Delhi & North
    'delhi':          'NDLS',    'new delhi':     'NDLS',
    'agra':           'AF',      'jaipur':        'JP',
    'lucknow':        'LKO',     'varanasi':      'BSB',
    'allahabad':      'ALD',     'kanpur':        'CNB',
    'chandigarh':     'CDG',     'amritsar':      'ASR',
    'ludhiana':       'LDH',
    # East
    'kolkata':        'HWH',     'calcutta':      'HWH',
    'howrah':         'HWH',     'patna':         'PNBE',
    'bhubaneswar':    'BBS',     'cuttack':       'CTC',
    'guwahati':       'GHY',
    # Gujarat & Rajasthan
    'ahmedabad':      'ADI',     'surat':         'ST',
    'vadodara':       'BRC',     'rajkot':        'RJT',
    'jodhpur':        'JU',      'udaipur':       'UDZ',
    # Goa / MP
    'goa':            'MAO',     'panaji':        'MAO',
    'bhopal':         'BPL',     'indore':        'INDB',
}

# Station code → (name, city, lat, lng)
STATION_INFO = {
    'MDU': ('Madurai Junction',        'Madurai',         9.9194,  78.1183),
    'MAS': ('Chennai Central',         'Chennai',        13.0833,  80.2750),
    'MS':  ('Chennai Egmore',          'Chennai',        13.0778,  80.2678),
    'CBE': ('Coimbatore Junction',     'Coimbatore',     11.0012,  76.9643),
    'TPJ': ('Tiruchirappalli Jn',      'Trichy',         10.8231,  78.6872),
    'TEN': ('Tirunelveli Junction',    'Tirunelveli',     8.7343,  77.7010),
    'SA':  ('Salem Junction',          'Salem',          11.6616,  78.1502),
    'ED':  ('Erode Junction',          'Erode',          11.3421,  77.7088),
    'SBC': ('KSR Bengaluru City Jn',   'Bengaluru',      12.9762,  77.5667),
    'MYS': ('Mysuru Junction',         'Mysuru',         12.2958,  76.6394),
    'MAQ': ('Mangaluru Central',       'Mangaluru',      12.8673,  74.8432),
    'SC':  ('Secunderabad Junction',   'Hyderabad',      17.4358,  78.5013),
    'BZA': ('Vijayawada Junction',     'Vijayawada',     16.5193,  80.6167),
    'VSKP':('Visakhapatnam',           'Visakhapatnam',  17.6868,  83.2186),
    'ERS': ('Ernakulam Junction',      'Kochi',          10.0097,  76.2970),
    'TVC': ('Thiruvananthapuram C.',   'Thiruvananthapuram', 8.4869, 76.9520),
    'CLT': ('Kozhikode',               'Kozhikode',      11.2411,  75.7816),
    'CSMT':('Mumbai CSMT',             'Mumbai',         18.9401,  72.8353),
    'PUNE':('Pune Junction',           'Pune',           18.5284,  73.8744),
    'NDLS':('New Delhi',               'Delhi',          28.6424,  77.2196),
    'HWH': ('Howrah Junction',         'Kolkata',        22.5839,  88.3424),
    'TN':  ('Thoothukudi',             'Thoothukudi',     8.7651,  78.1352),
    'DG':  ('Dindigul Junction',       'Dindigul',       10.3597,  77.9877),
    'VPT': ('Virudhunagar Junction',   'Virudhunagar',    9.5816,  77.9641),
    'NCJ': ('Nagercoil Junction',      'Nagercoil',       8.1693,  77.4167),
    'TUP': ('Tiruppur',                'Tiruppur',       11.1121,  77.3561),
    'TJ':  ('Thanjavur Junction',      'Thanjavur',      10.7869,  79.1372),
    'SVPR':('Srivilliputtur',           'Srivilliputhur',  9.5121,  77.6336),
    'SVKS':('Sivakasi',                'Sivakasi',        9.4514,  77.8086),
    'RJPM':('Rajapalayam',             'Rajapalayam',     9.4529,  77.5568),
    'TNKV':('Tenkasi Junction',        'Tenkasi',         8.9593,  77.3152),
    'CVP': ('Kovilpatti',              'Kovilpatti',      9.1765,  77.8697),
    'ARPT':('Aruppukkottai',           'Aruppukkottai',   9.5075,  78.0964),
    # WRJ (Watrap) removed — narrow gauge line, no major express trains
    'ALU': ('Alangulam',               'Alangulam',       8.8702,  77.4143),
    'KTMD':('Katpadi Junction',        'Vellore',        12.9165,  79.1325),
    'HSRA':('Hosur',                   'Hosur',          12.7349,  77.8305),
    'VLR': ('Vellore Cantonment',      'Vellore',        12.9165,  79.1325),
    'TPTY':('Tirupati',                'Tirupati',       13.6288,  79.4192),
    'LKO': ('Lucknow Charbagh',        'Lucknow',        26.8318,  80.9059),
    'PNBE':('Patna Junction',          'Patna',          25.5971,  85.1793),
    'BBS': ('Bhubaneswar',             'Bhubaneswar',    20.2539,  85.8121),
    'ADI': ('Ahmedabad Junction',      'Ahmedabad',      23.0713,  72.5993),
    'JP':  ('Jaipur Junction',         'Jaipur',         26.9193,  75.7879),
}


@app.route('/api/live-trains')
def api_live_trains():
    """
    Fetch trains between stations via IRCTC RapidAPI.
    Requires IRCTC_RAPIDAPI_KEY environment variable.
    Free tier: https://rapidapi.com/IRCTCAPI/api/irctc1
    """
    origin_city  = request.args.get('origin', '').strip()
    dest_city    = request.args.get('destination', '').strip()
    # Normalize alternate spellings
    _SPELL = {
        'krishnankoil':'krishnankovil','krishnan koil':'krishnankovil',
        'krishnan kovil':'krishnankovil','srivilliputtur':'srivilliputhur',
        'bangalore':'bengaluru','trivandrum':'thiruvananthapuram',
        'bombay':'mumbai','calcutta':'kolkata','madras':'chennai',
    }
    origin_lower = _SPELL.get(origin_city.lower(), origin_city.lower())
    dest_lower   = _SPELL.get(dest_city.lower(),   dest_city.lower())
    journey_date = request.args.get('date', '')
    if not journey_date:
        from datetime import date as _date
        journey_date = _date.today().strftime('%Y-%m-%d')

    from_code = CITY_TO_STATION.get(origin_lower)
    to_code   = CITY_TO_STATION.get(dest_lower)

    # Partial text match
    if not from_code:
        for k, v in CITY_TO_STATION.items():
            if origin_lower in k or k in origin_lower:
                from_code = v; break
    if not to_code:
        for k, v in CITY_TO_STATION.items():
            if dest_lower in k or k in dest_lower:
                to_code = v; break

    from_nearest_used = False
    to_nearest_used   = False
    from_nearest_dist = None
    to_nearest_dist   = None

    # Geo fallback: find nearest station when city is not mapped
    if not from_code:
        nc, ni, nd = _nearest_station(origin_city)
        if nc:
            from_code = nc
            from_nearest_used = True
            from_nearest_dist = nd

    if not to_code:
        nc, ni, nd = _nearest_station(dest_city)
        if nc:
            to_code = nc
            to_nearest_used = True
            to_nearest_dist = nd

    from_info = STATION_INFO.get(from_code, ())
    to_info   = STATION_INFO.get(to_code, ())

    if requests is None:
        return jsonify({'status': 'error', 'message': 'requests library not installed', 'trains': []})

    api_key = os.getenv('ERAIL_API_KEY', 'rr_a28ltnhisoix4sai2aavfwy3vk2lprsa').strip()

    if not api_key:
        return jsonify({
            'status': 'no_key',
            'message': 'erail.in API key not configured.',
            'from_code': from_code, 'to_code': to_code,
            'trains': [], 'setup_url': 'https://erail.in',
        })

    if not from_code or not to_code:
        return jsonify({
            'status': 'no_station',
            'message': f'Could not find any railway station near {"origin" if not from_code else "destination"} city.',
            'from_code': from_code, 'to_code': to_code,
            'trains': [],
        })

    # erail.in class flag positions (15-char binary string)
    _CLASS_FLAGS = ['1A','2A','3A','FC','SL','3E','2S','CC','EX','PC','HS','VS','SV','GN','VO']

    def _parse_classes(flag_str):
        classes = []
        for i, ch in enumerate(flag_str or ''):
            if ch == '1' and i < len(_CLASS_FLAGS):
                classes.append(_CLASS_FLAGS[i])
        return classes

    def _parse_run_days(day_str):
        names = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
        return [names[i] for i, ch in enumerate(day_str or '') if ch == '1' and i < 7]

    def _calc_duration(dep, arr):
        """Calculate HH:MM duration from erail.in time strings (HH.MM format)."""
        try:
            dh, dm = int(dep.split('.')[0]), int(dep.split('.')[1])
            ah, am = int(arr.split('.')[0]), int(arr.split('.')[1])
            total = (ah * 60 + am) - (dh * 60 + dm)
            if total < 0:
                total += 24 * 60   # overnight
            return f'{total // 60}h {total % 60:02d}m'
        except Exception:
            return ''

    def _fetch_trains_erail(src_code, dst_code, erail_date, timeout=20):
        """Call erail.in and return list of parsed train dicts (empty list on failure)."""
        url = (f'https://erail.in/rail/getTrains.aspx'
               f'?TrainNo=&Station_From={src_code}&Station_To={dst_code}'
               f'&TrainDate={erail_date}&ServiceID=4&STFLAG=Y&ENDFLAG=Y&LangID=0&APIKey={api_key}')
        resp = requests.get(url, timeout=timeout)
        if resp.status_code != 200 or not resp.text.strip():
            return []
        raw_records = resp.text.strip().split('^')
        trains = []
        for rec in raw_records[1:]:
            f = rec.strip().split('~')
            if len(f) < 12:
                continue
            dep     = f[10].strip()
            arr     = f[11].strip()
            dep_fmt = dep.replace('.', ':') if '.' in dep else dep
            arr_fmt = arr.replace('.', ':') if '.' in arr else arr
            trains.append({
                'number':            f[0].strip(),
                'name':              f[1].strip(),
                'train_origin':      f[6].strip(),
                'train_origin_code': f[7].strip(),
                'from_station':      src_code,
                'to_station':        dst_code,
                'to_name':           f[8].strip(),
                'dep_time':     dep_fmt,
                'arr_time':     arr_fmt,
                'duration':     _calc_duration(dep, arr),
                'run_days':     _parse_run_days(f[13].strip() if len(f) > 13 else ''),
                'classes':      _parse_classes(f[21].strip() if len(f) > 21 else ''),
                'train_type':   f[32].strip() if len(f) > 32 else '',
                'distance':     f[40].strip() if len(f) > 40 else '',
            })
        return trains

    try:
        # erail.in date format: DDMMYYYY
        from datetime import datetime as _dt
        try:
            dt_obj = _dt.strptime(journey_date, '%Y-%m-%d')
            erail_date = dt_obj.strftime('%d%m%Y')
        except Exception:
            erail_date = journey_date.replace('-', '')

        trains = _fetch_trains_erail(from_code, to_code, erail_date)

        # Track the original station before any retry
        original_from_code = from_code
        original_from_name = from_info[0] if from_info else from_code
        station_switched   = False   # True when we fell back to a different station

        # ── Auto-retry: if 0 trains found from ANY station (direct or nearest),
        #    try up to 5 nearby stations sorted by distance ──────────────────────
        if not trains:
            orig_coords = CITY_COORDS.get(origin_lower)
            if orig_coords:
                import math as _m
                tried = {from_code}
                candidates = []
                for code, info in STATION_INFO.items():
                    if code in tried or len(info) < 4:
                        continue
                    dlat = _m.radians(info[2] - orig_coords[0])
                    dlon = _m.radians(info[3] - orig_coords[1])
                    a = (_m.sin(dlat/2)**2 +
                         _m.cos(_m.radians(orig_coords[0])) * _m.cos(_m.radians(info[2])) *
                         _m.sin(dlon/2)**2)
                    d = 6371 * 2 * _m.asin(_m.sqrt(a))
                    if d <= 200:
                        candidates.append((d, code, info))
                candidates.sort(key=lambda x: x[0])

                for alt_dist, alt_code, alt_info in candidates[:5]:
                    try:
                        alt_trains = _fetch_trains_erail(alt_code, to_code, erail_date, timeout=10)
                    except Exception:
                        alt_trains = []
                    if alt_trains:
                        trains           = alt_trains
                        from_code        = alt_code
                        from_info        = alt_info
                        from_nearest_dist = round(alt_dist, 1)
                        from_nearest_used = True
                        station_switched  = True
                        break

        return jsonify({
            'status': 'ok',
            'from_code': from_code, 'to_code': to_code,
            'from_name': from_info[0] if from_info else from_code,
            'to_name':   to_info[0]   if to_info   else to_code,
            'trains':    trains,
            'count':     len(trains),
            'date':      journey_date,
            'from_nearest_used':   from_nearest_used,
            'from_nearest_dist':   from_nearest_dist,
            'to_nearest_used':     to_nearest_used,
            'to_nearest_dist':     to_nearest_dist,
            'origin_city':         origin_city,
            'dest_city':           dest_city,
            'station_switched':    station_switched,
            'original_from_code':  original_from_code,
            'original_from_name':  original_from_name,
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'trains': []})


# ─────────────────────────────────────────────────────────────────────────────
# Admin Timetable Management
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/admin/timetables')
@login_required
def admin_timetables():
    """Admin page to view and manage all bus timetable entries."""
    if not current_user.is_admin:
        abort(403)
    local_buses = LocalBus.query.order_by(LocalBus.origin, LocalBus.departure_time).all()
    private_buses = PrivateOperator.query.order_by(PrivateOperator.origin, PrivateOperator.departure_time).all()
    return render_template('manage_timetables.html',
                           local_buses=local_buses,
                           private_buses=private_buses)


@app.route('/admin/timetables/add-local', methods=['POST'])
@login_required
def admin_add_local_bus():
    """Add a single local (government) bus entry."""
    if not current_user.is_admin:
        abort(403)
    try:
        dep_str = request.form.get('departure_time', '').strip()
        arr_str = request.form.get('arrival_time', '').strip()
        dep_time = datetime.strptime(dep_str, '%H:%M').time() if dep_str else None
        arr_time = datetime.strptime(arr_str, '%H:%M').time() if arr_str else None
        if not dep_time or not arr_time:
            flash('Departure and arrival times are required.', 'danger')
            return redirect(url_for('admin_timetables'))
        via_raw = request.form.get('via_stops', '').strip()
        via_stops = [s.strip() for s in via_raw.split(';') if s.strip()] if via_raw else []
        bus = LocalBus(
            bus_number=request.form.get('bus_number', '').strip(),
            route_number=request.form.get('route_number', '').strip(),
            operator=request.form.get('operator', 'TNSTC').strip(),
            origin=request.form.get('origin', '').strip(),
            destination=request.form.get('destination', '').strip(),
            departure_time=dep_time,
            arrival_time=arr_time,
            via_stops=via_stops,
            fare=float(request.form.get('fare') or 0),
            bus_type=request.form.get('bus_type', 'Ordinary').strip(),
            status=request.form.get('status', 'scheduled').strip(),
            seat_availability=int(request.form.get('seat_availability') or 50),
            total_seats=int(request.form.get('total_seats') or 50),
            is_active=True
        )
        db.session.add(bus)
        db.session.commit()
        flash(f'Local bus {bus.bus_number} added successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding local bus: {e}', 'danger')
    return redirect(url_for('admin_timetables'))


@app.route('/admin/timetables/add-private', methods=['POST'])
@login_required
def admin_add_private_bus():
    """Add a single private bus operator entry."""
    if not current_user.is_admin:
        abort(403)
    try:
        dep_str = request.form.get('departure_time', '').strip()
        arr_str = request.form.get('arrival_time', '').strip()
        dep_time = datetime.strptime(dep_str, '%H:%M').time() if dep_str else None
        arr_time = datetime.strptime(arr_str, '%H:%M').time() if arr_str else None
        if not dep_time or not arr_time:
            flash('Departure and arrival times are required.', 'danger')
            return redirect(url_for('admin_timetables'))
        via_raw = request.form.get('via_stops', '').strip()
        via_stops = [s.strip() for s in via_raw.split(';') if s.strip()] if via_raw else []
        origin = request.form.get('origin', '').strip()
        destination = request.form.get('destination', '').strip()
        bus = PrivateOperator(
            operator_name=request.form.get('operator_name', '').strip(),
            bus_number=request.form.get('bus_number', '').strip(),
            route_name=f"{origin} \u2013 {destination}",
            origin=origin,
            destination=destination,
            departure_time=dep_time,
            arrival_time=arr_time,
            via_stops=via_stops,
            fare=float(request.form.get('fare') or 0),
            bus_type=request.form.get('bus_type', 'AC Seater').strip(),
            status=request.form.get('status', 'available').strip(),
            rating=float(request.form.get('rating') or 0),
            live_tracking=request.form.get('live_tracking') == 'yes',
            duration=request.form.get('duration', '').strip(),
            seat_availability=int(request.form.get('seat_availability') or 40),
            total_seats=int(request.form.get('total_seats') or 40),
            is_active=True
        )
        db.session.add(bus)
        db.session.commit()
        flash(f'Private bus {bus.bus_number} added successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding private bus: {e}', 'danger')
    return redirect(url_for('admin_timetables'))


@app.route('/admin/timetables/delete-local/<int:bus_id>', methods=['POST'])
@login_required
def admin_delete_local_bus(bus_id):
    """Delete a local bus entry."""
    if not current_user.is_admin:
        abort(403)
    bus = LocalBus.query.get_or_404(bus_id)
    try:
        db.session.delete(bus)
        db.session.commit()
        flash(f'Local bus {bus.bus_number} deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting: {e}', 'danger')
    return redirect(url_for('admin_timetables'))


@app.route('/admin/timetables/delete-private/<int:bus_id>', methods=['POST'])
@login_required
def admin_delete_private_bus(bus_id):
    """Delete a private bus entry."""
    if not current_user.is_admin:
        abort(403)
    bus = PrivateOperator.query.get_or_404(bus_id)
    try:
        db.session.delete(bus)
        db.session.commit()
        flash(f'Private bus {bus.bus_number} deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting: {e}', 'danger')
    return redirect(url_for('admin_timetables'))


@app.route('/admin/timetables/clear-all', methods=['POST'])
@login_required
def admin_clear_timetables():
    """Remove ALL existing local and private bus data."""
    if not current_user.is_admin:
        abort(403)
    try:
        local_count = LocalBus.query.count()
        private_count = PrivateOperator.query.count()
        LocalBus.query.delete()
        PrivateOperator.query.delete()
        db.session.commit()
        flash(f'Cleared {local_count} local bus entries and {private_count} private bus entries.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error clearing data: {e}', 'danger')
    return redirect(url_for('admin_timetables'))


@app.route('/admin/timetables/upload-csv', methods=['POST'])
@login_required
def admin_upload_timetable_csv():
    """
    Upload a CSV file to bulk-add bus timetable entries.

    Required CSV columns (first row = header):
    type, bus_number, operator, origin, destination, departure_time, arrival_time,
    fare, bus_type, status, via_stops, route_number, route_name, rating,
    live_tracking, duration, seat_availability, total_seats

    type must be 'local' or 'private'.
    via_stops: semicolon-separated list  e.g. Salem;Erode
    departure_time / arrival_time: HH:MM (24-hour)
    live_tracking: yes / no
    """
    if not current_user.is_admin:
        abort(403)
    file = request.files.get('csv_file')
    if not file or not file.filename.lower().endswith('.csv'):
        flash('Please upload a valid .csv file.', 'danger')
        return redirect(url_for('admin_timetables'))
    try:
        stream = io.StringIO(file.stream.read().decode('utf-8-sig'), newline=None)
        reader = csv.DictReader(stream)
        added_local = 0
        added_private = 0
        errors = []
        for row_num, row in enumerate(reader, start=2):
            bus_type_entry = row.get('type', '').strip().lower()
            try:
                dep_time = datetime.strptime(row.get('departure_time', '').strip(), '%H:%M').time()
                arr_time = datetime.strptime(row.get('arrival_time', '').strip(), '%H:%M').time()
            except ValueError:
                errors.append(f'Row {row_num}: invalid time format (use HH:MM).')
                continue
            via_raw = row.get('via_stops', '').strip()
            via_stops = [s.strip() for s in via_raw.split(';') if s.strip()]
            fare = float(row.get('fare') or 0)
            if bus_type_entry == 'local':
                entry = LocalBus(
                    bus_number=row.get('bus_number', '').strip(),
                    route_number=row.get('route_number', '').strip(),
                    operator=row.get('operator', 'TNSTC').strip(),
                    origin=row.get('origin', '').strip(),
                    destination=row.get('destination', '').strip(),
                    departure_time=dep_time,
                    arrival_time=arr_time,
                    via_stops=via_stops,
                    fare=fare,
                    bus_type=row.get('bus_type', 'Ordinary').strip(),
                    status=row.get('status', 'scheduled').strip(),
                    seat_availability=int(row.get('seat_availability') or 50),
                    total_seats=int(row.get('total_seats') or 50),
                    is_active=True
                )
                db.session.add(entry)
                added_local += 1
            elif bus_type_entry == 'private':
                origin = row.get('origin', '').strip()
                destination = row.get('destination', '').strip()
                route_name = row.get('route_name', '').strip() or f"{origin} \u2013 {destination}"
                entry = PrivateOperator(
                    operator_name=row.get('operator', '').strip(),
                    bus_number=row.get('bus_number', '').strip(),
                    route_name=route_name,
                    origin=origin,
                    destination=destination,
                    departure_time=dep_time,
                    arrival_time=arr_time,
                    via_stops=via_stops,
                    fare=fare,
                    bus_type=row.get('bus_type', 'AC Seater').strip(),
                    status=row.get('status', 'available').strip(),
                    rating=float(row.get('rating') or 0),
                    live_tracking=(row.get('live_tracking', 'no').strip().lower() == 'yes'),
                    duration=row.get('duration', '').strip(),
                    seat_availability=int(row.get('seat_availability') or 40),
                    total_seats=int(row.get('total_seats') or 40),
                    is_active=True
                )
                db.session.add(entry)
                added_private += 1
            else:
                errors.append(f'Row {row_num}: unknown type "{bus_type_entry}" (use local or private).')
        db.session.commit()
        msg = f'CSV imported: {added_local} local, {added_private} private bus entries added.'
        if errors:
            msg += '  Skipped: ' + ' | '.join(errors)
        flash(msg, 'success' if not errors else 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f'Error processing CSV: {e}', 'danger')
    return redirect(url_for('admin_timetables'))


@app.route('/admin/timetables/upload-csv-local', methods=['POST'])
@login_required
def admin_upload_local_csv():
    """Upload a CSV file to bulk-add government (local) bus entries only.
    Columns: bus_number, route_number, operator, origin, destination,
             departure_time, arrival_time, fare, bus_type, status,
             via_stops, seat_availability, total_seats
    """
    if not current_user.is_admin:
        abort(403)
    file = request.files.get('csv_file')
    if not file or not file.filename.lower().endswith('.csv'):
        flash('Please upload a valid .csv file.', 'danger')
        return redirect(url_for('admin_timetables'))
    try:
        stream = io.StringIO(file.stream.read().decode('utf-8-sig'), newline=None)
        reader = csv.DictReader(stream)
        added = 0
        errors = []
        for row_num, row in enumerate(reader, start=2):
            try:
                dep_time = datetime.strptime(row.get('departure_time', '').strip(), '%H:%M').time()
                arr_time = datetime.strptime(row.get('arrival_time', '').strip(), '%H:%M').time()
            except ValueError:
                errors.append(f'Row {row_num}: invalid time format (use HH:MM).')
                continue
            via_raw = row.get('via_stops', '').strip()
            via_stops = [s.strip() for s in via_raw.split(';') if s.strip()]
            entry = LocalBus(
                bus_number=row.get('bus_number', '').strip(),
                route_number=row.get('route_number', '').strip(),
                operator=row.get('operator', 'TNSTC').strip(),
                origin=row.get('origin', '').strip(),
                destination=row.get('destination', '').strip(),
                departure_time=dep_time,
                arrival_time=arr_time,
                via_stops=via_stops,
                fare=float(row.get('fare') or 0),
                bus_type=row.get('bus_type', 'Ordinary').strip(),
                status=row.get('status', 'scheduled').strip(),
                seat_availability=int(row.get('seat_availability') or 50),
                total_seats=int(row.get('total_seats') or 50),
                is_active=True
            )
            db.session.add(entry)
            added += 1
        db.session.commit()
        msg = f'Imported {added} government bus entries.'
        if errors:
            msg += '  Skipped: ' + ' | '.join(errors)
        flash(msg, 'success' if not errors else 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f'Error processing CSV: {e}', 'danger')
    return redirect(url_for('admin_timetables'))


@app.route('/admin/timetables/upload-csv-private', methods=['POST'])
@login_required
def admin_upload_private_csv():
    """Upload a CSV file to bulk-add private bus entries only.
    Columns: bus_number, operator_name, origin, destination,
             departure_time, arrival_time, fare, bus_type, status,
             via_stops, route_name, rating, live_tracking, duration,
             seat_availability, total_seats
    """
    if not current_user.is_admin:
        abort(403)
    file = request.files.get('csv_file')
    if not file or not file.filename.lower().endswith('.csv'):
        flash('Please upload a valid .csv file.', 'danger')
        return redirect(url_for('admin_timetables'))
    try:
        stream = io.StringIO(file.stream.read().decode('utf-8-sig'), newline=None)
        reader = csv.DictReader(stream)
        added = 0
        errors = []
        for row_num, row in enumerate(reader, start=2):
            try:
                dep_time = datetime.strptime(row.get('departure_time', '').strip(), '%H:%M').time()
                arr_time = datetime.strptime(row.get('arrival_time', '').strip(), '%H:%M').time()
            except ValueError:
                errors.append(f'Row {row_num}: invalid time format (use HH:MM).')
                continue
            via_raw = row.get('via_stops', '').strip()
            via_stops = [s.strip() for s in via_raw.split(';') if s.strip()]
            origin = row.get('origin', '').strip()
            destination = row.get('destination', '').strip()
            route_name = row.get('route_name', '').strip() or f"{origin} \u2013 {destination}"
            entry = PrivateOperator(
                operator_name=row.get('operator_name', '').strip(),
                bus_number=row.get('bus_number', '').strip(),
                route_name=route_name,
                origin=origin,
                destination=destination,
                departure_time=dep_time,
                arrival_time=arr_time,
                via_stops=via_stops,
                fare=float(row.get('fare') or 0),
                bus_type=row.get('bus_type', 'AC Seater').strip(),
                status=row.get('status', 'available').strip(),
                rating=float(row.get('rating') or 0),
                live_tracking=(row.get('live_tracking', 'no').strip().lower() == 'yes'),
                duration=row.get('duration', '').strip(),
                seat_availability=int(row.get('seat_availability') or 40),
                total_seats=int(row.get('total_seats') or 40),
                is_active=True
            )
            db.session.add(entry)
            added += 1
        db.session.commit()
        msg = f'Imported {added} private bus entries.'
        if errors:
            msg += '  Skipped: ' + ' | '.join(errors)
        flash(msg, 'success' if not errors else 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f'Error processing CSV: {e}', 'danger')
    return redirect(url_for('admin_timetables'))


@app.route('/admin/timetables/download-template-local')
@login_required
def admin_download_local_csv_template():
    """Download a sample CSV template for government buses."""
    if not current_user.is_admin:
        abort(403)
    from flask import Response
    sample = (
        "bus_number,route_number,operator,origin,destination,departure_time,arrival_time,"
        "fare,bus_type,status,via_stops,seat_availability,total_seats\n"
        "TN72A0001,SVP-001,TNSTC,Srivilliputhur,Madurai,06:00,08:30,80,Express,scheduled,Virudhunagar,50,50\n"
        "TN72A0002,SVP-002,SETC,Madurai,Chennai,21:00,06:00,350,Ultra Deluxe,scheduled,Trichy;Salem,45,50\n"
    )
    return Response(
        sample,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=govt_bus_template.csv'}
    )


@app.route('/admin/timetables/download-template-private')
@login_required
def admin_download_private_csv_template():
    """Download a sample CSV template for private buses."""
    if not current_user.is_admin:
        abort(403)
    from flask import Response
    sample = (
        "bus_number,operator_name,origin,destination,departure_time,arrival_time,"
        "fare,bus_type,status,via_stops,route_name,rating,live_tracking,duration,"
        "seat_availability,total_seats\n"
        "KA01P001,KPN Travels,Madurai,Chennai,21:00,07:00,600,AC Sleeper,available,"
        "Salem;Trichy,Madurai - Chennai,4.5,yes,10h,40,40\n"
        "KA01P002,SRS Travels,Madurai,Bengaluru,20:00,06:00,800,Volvo AC,available,"
        "Dindigul;Salem,Madurai - Bengaluru,4.3,yes,10h,36,40\n"
    )
    return Response(
        sample,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=private_bus_template.csv'}
    )


@app.route('/admin/timetables/download-template')
@login_required
def admin_download_csv_template():
    """Download a sample CSV template for bulk bus upload."""
    if not current_user.is_admin:
        abort(403)
    from flask import Response
    sample = (
        "type,bus_number,operator,origin,destination,departure_time,arrival_time,"
        "fare,bus_type,status,via_stops,route_number,route_name,rating,live_tracking,duration,"
        "seat_availability,total_seats\n"
        "local,TN72A0001,TNSTC,Srivilliputhur,Madurai,06:00,08:30,"
        "80,Express,scheduled,Virudhunagar,SVP-001,,0,no,,50,50\n"
        "private,KA01P001,KPN Travels,Madurai,Chennai,21:00,07:00,"
        "600,AC Sleeper,available,Salem;Trichy,,Madurai - Chennai,4.5,yes,10h,40,40\n"
    )
    return Response(
        sample,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=timetable_template.csv'}
    )


# ── Admin Models (AI / ML control center) ────────────────────────────────────
def _require_admin_models():
    if not current_user.is_authenticated or not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return False
    return True


@app.route('/admin/models')
@login_required
def admin_models():
    if not _require_admin_models():
        return redirect(url_for('dashboard'))
    from ml.models_catalog import MODEL_CATEGORIES
    return render_template('models/index.html', model_categories=MODEL_CATEGORIES)


def _render_model_page(slug):
    if not _require_admin_models():
        return redirect(url_for('dashboard'))
    from ml.models_catalog import MODEL_DETAILS
    model = MODEL_DETAILS.get(slug)
    if not model:
        abort(404)
    results = None
    if slug == 'lstm':
        from ml.demand_forecasting import load_lstm_results
        results = load_lstm_results()
    elif slug == 'xgboost':
        from ml.demand_forecasting import load_xgboost_results
        results = load_xgboost_results()
    elif slug == 'hybrid':
        from ml.demand_forecasting import load_hybrid_results
        results = load_hybrid_results()
    return render_template(
        model.get('template', 'models/model_detail.html'),
        model=model,
        results=results,
    )


@app.route('/admin/models/lstm')
@login_required
def admin_models_lstm():
    return _render_model_page('lstm')


@app.route('/admin/models/lstm/results.json')
@login_required
def admin_models_lstm_results_json():
    if not _require_admin_models():
        return jsonify({'error': 'unauthorized'}), 403
    from ml.demand_forecasting import load_lstm_results, run_realtime_inference
    results = load_lstm_results()
    live = {'ok': False, 'live_comparison': [], 'live_forecast': []}
    if results.get('trained'):
        try:
            live = run_realtime_inference(
                session=db.session,
                recent_hours=48,
                forecast_horizon=24,
            )
        except Exception as e:
            logging.exception('Realtime LSTM inference failed')
            live = {
                'ok': False,
                'error': str(e),
                'live_comparison': results.get('comparison') or [],
                'live_forecast': results.get('forecast') or [],
            }
    return jsonify({
        'trained': bool(results.get('trained')),
        'metrics': results.get('metrics'),
        'history': results.get('history'),
        # Prefer live inference tables; fall back to last training artifacts
        'comparison': live.get('live_comparison') or results.get('comparison') or [],
        'forecast': live.get('live_forecast') or results.get('forecast') or [],
        'live': live,
        'metadata': results.get('metadata'),
        'realtime': True,
        'generated_at': live.get('generated_at'),
    })


@app.route('/admin/models/lstm/dataset.csv')
@login_required
def admin_models_lstm_dataset_csv():
    """Download the operational hourly demand dataset used for LSTM training."""
    if not _require_admin_models():
        return redirect(url_for('dashboard'))
    import os
    from flask import send_file
    candidates = [
        os.path.join(app.root_path, 'ml_results', 'lstm', 'training_series.csv'),
        os.path.join(app.root_path, 'data', 'lstm_hourly_demand_dataset.csv'),
    ]
    path = next((p for p in candidates if os.path.isfile(p)), None)
    if not path:
        flash('Dataset file not found. Train the LSTM once to generate it.', 'warning')
        return redirect(url_for('admin_models_lstm'))
    return send_file(
        path,
        mimetype='text/csv',
        as_attachment=True,
        download_name='lstm_hourly_demand_dataset.csv',
    )


@app.route('/admin/models/lstm/train', methods=['POST'])
@login_required
def admin_models_lstm_train():
    if not _require_admin_models():
        return redirect(url_for('dashboard'))
    try:
        from ml.demand_forecasting import train_lstm_pipeline
        from ml.demand_forecasting.preprocessing import ensure_operational_demand

        prep = ensure_operational_demand(db.session, days=90)
        result = train_lstm_pipeline(
            session=db.session,
            config={
                'epochs': 25,
                'batch_size': 32,
                'sequence_length': 24,
                'lstm_units_1': 64,
                'lstm_units_2': 32,
                'dropout': 0.2,
                'learning_rate': 0.001,
                'validation_split': 0.1,
                'early_stopping_patience': 5,
                'forecast_horizon': 24,
            },
        )
        metrics = result.get('metrics') or {}
        data_meta = (result.get('metadata') or {}).get('data') or {}
        flash(
            f"LSTM trained successfully.\n\n"
            f"Dataset: {data_meta.get('dataset_name')}\n\n"
            f"MAE = {metrics.get('mae')}\n"
            f"RMSE = {metrics.get('rmse')}\n"
            f"MAPE = {metrics.get('mape')}%\n"
            f"R² = {metrics.get('r2')}",
            'success',
        )
    except Exception as e:
        logging.exception('LSTM training failed')
        flash(f'LSTM training failed: {e}', 'danger')
    return redirect(url_for('admin_models_lstm'))


@app.route('/admin/models/xgboost')
@login_required
def admin_models_xgboost():
    return _render_model_page('xgboost')


@app.route('/admin/models/xgboost/results.json')
@login_required
def admin_models_xgboost_results_json():
    if not _require_admin_models():
        return jsonify({'error': 'unauthorized'}), 403
    from ml.demand_forecasting import load_xgboost_results, run_realtime_xgboost_inference
    results = load_xgboost_results()
    live = {'ok': False, 'live_comparison': [], 'live_forecast': []}
    if results.get('trained'):
        try:
            live = run_realtime_xgboost_inference(
                session=db.session,
                recent_hours=24,
                forecast_hours=24,
            )
        except Exception as e:
            logging.exception('Realtime XGBoost inference failed')
            live = {
                'ok': False,
                'error': str(e),
                'live_comparison': results.get('comparison') or [],
                'live_forecast': [],
            }
    return jsonify({
        'trained': bool(results.get('trained')),
        'metrics': results.get('metrics'),
        # Prefer live IST tables; fall back to last training test window
        'comparison': live.get('live_comparison') or results.get('comparison') or [],
        'forecast': live.get('live_forecast') or [],
        'feature_importance': results.get('feature_importance') or [],
        'importance_type': results.get('importance_type'),
        'metadata': results.get('metadata'),
        'live': live,
        'realtime': True,
        'generated_at': live.get('generated_at'),
        'now_ist': live.get('now_ist'),
    })


@app.route('/admin/models/xgboost/dataset.csv')
@login_required
def admin_models_xgboost_dataset_csv():
    if not _require_admin_models():
        return redirect(url_for('dashboard'))
    import os
    from flask import send_file
    path = os.path.join(app.root_path, 'ml_results', 'xgboost', 'structured_training_table.csv')
    if not os.path.isfile(path):
        flash('XGBoost dataset not found. Train the model once to generate it.', 'warning')
        return redirect(url_for('admin_models_xgboost'))
    return send_file(
        path,
        mimetype='text/csv',
        as_attachment=True,
        download_name='xgboost_structured_demand_dataset.csv',
    )


@app.route('/admin/models/xgboost/train', methods=['POST'])
@login_required
def admin_models_xgboost_train():
    if not _require_admin_models():
        return redirect(url_for('dashboard'))
    try:
        from ml.demand_forecasting import train_xgboost_pipeline
        result = train_xgboost_pipeline(
            session=db.session,
            config={
                'n_estimators': 250,
                'max_depth': 6,
                'learning_rate': 0.05,
                'subsample': 0.85,
                'colsample_bytree': 0.85,
                'min_child_weight': 3,
                'reg_alpha': 0.05,
                'reg_lambda': 1.0,
                'early_stopping_rounds': 25,
            },
        )
        metrics = result.get('metrics') or {}
        data_meta = (result.get('metadata') or {}).get('data') or {}
        flash(
            f"XGBoost trained successfully.\n\n"
            f"Dataset: {data_meta.get('dataset_name')} ({data_meta.get('dataset_type', 'OPERATIONAL')})\n\n"
            f"MAE = {metrics.get('mae')}\n"
            f"RMSE = {metrics.get('rmse')}\n"
            f"MAPE = {metrics.get('mape')}%\n"
            f"R² = {metrics.get('r2')}",
            'success',
        )
    except Exception as e:
        logging.exception('XGBoost training failed')
        flash(f'XGBoost training failed: {e}', 'danger')
    return redirect(url_for('admin_models_xgboost'))


@app.route('/admin/models/hybrid-lstm-xgboost')
@login_required
def admin_models_hybrid():
    return _render_model_page('hybrid')


@app.route('/admin/models/hybrid-lstm-xgboost/results.json')
@login_required
def admin_models_hybrid_results_json():
    if not _require_admin_models():
        return jsonify({'error': 'unauthorized'}), 403
    from ml.demand_forecasting import load_hybrid_results
    results = load_hybrid_results()
    return jsonify({
        'trained': bool(results.get('trained')),
        'metrics': results.get('metrics'),
        'comparison': results.get('comparison'),
        'predictions': results.get('predictions') or [],
        'metadata': results.get('metadata'),
    })


@app.route('/admin/models/hybrid-lstm-xgboost/predictions.csv')
@login_required
def admin_models_hybrid_predictions_csv():
    if not _require_admin_models():
        return redirect(url_for('dashboard'))
    import os
    from flask import send_file
    path = os.path.join(app.root_path, 'ml_results', 'hybrid', 'predictions.csv')
    if not os.path.isfile(path):
        flash('Hybrid predictions not found. Train the hybrid model once to generate them.', 'warning')
        return redirect(url_for('admin_models_hybrid'))
    return send_file(
        path,
        mimetype='text/csv',
        as_attachment=True,
        download_name='hybrid_predictions.csv',
    )


@app.route('/admin/models/hybrid-lstm-xgboost/train', methods=['POST'])
@login_required
def admin_models_hybrid_train():
    if not _require_admin_models():
        return redirect(url_for('dashboard'))
    try:
        from ml.demand_forecasting import train_hybrid_pipeline
        from ml.demand_forecasting.preprocessing import ensure_operational_demand

        ensure_operational_demand(db.session, days=90)
        result = train_hybrid_pipeline(session=db.session)
        metrics = (result.get('metrics') or {}).get('hybrid') or {}
        data_meta = (result.get('metadata') or {}).get('data') or {}
        best = ((result.get('comparison') or {}).get('best') or {}).get('best_model', '—')
        flash(
            f"Hybrid LSTM + XGBoost trained successfully.\n\n"
            f"Dataset: {data_meta.get('dataset_name')} ({data_meta.get('dataset_type', 'OPERATIONAL')})\n\n"
            f"Hybrid MAE = {metrics.get('mae')}\n"
            f"Hybrid RMSE = {metrics.get('rmse')}\n"
            f"Hybrid MAPE = {metrics.get('mape')}%\n"
            f"Hybrid R² = {metrics.get('r2')}\n\n"
            f"Best model (test MAE): {best}",
            'success',
        )
    except Exception as e:
        logging.exception('Hybrid training failed')
        flash(f'Hybrid training failed: {e}', 'danger')
    return redirect(url_for('admin_models_hybrid'))


@app.route('/admin/models/nsga2')
@login_required
def admin_models_nsga2():
    return _render_model_page('nsga2')


@app.route('/admin/models/gnn')
@login_required
def admin_models_gnn():
    return _render_model_page('gnn')


@app.route('/admin/models/gis')
@login_required
def admin_models_gis():
    return _render_model_page('gis')


@app.route('/admin/models/recommendation')
@login_required
def admin_models_recommendation():
    return _render_model_page('recommendation')


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404
@app.errorhandler(500)
def internal_error(e):
    return render_template('500.html'), 500
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    import os as _os
    port = int(_os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)