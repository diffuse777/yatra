import os
from app import app, db, create_admin_user

def init_db():
    with app.app_context():
        # Create all database tables
        db.create_all()
        
        # Create admin user
        create_admin_user()
        
        print("Database initialized successfully!")

if __name__ == '__main__':
    # Delete existing database if it exists
    db_path = os.path.join('instance', 'bus_system.db')
    if os.path.exists(db_path):
        os.remove(db_path)
    
    # Create instance directory if it doesn't exist
    os.makedirs('instance', exist_ok=True)
    
    # Initialize the database
    init_db()
