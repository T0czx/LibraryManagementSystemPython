print("Starting app.py...")

from flask import Flask, render_template, request, redirect, url_for, session, flash
print("Imported Flask and dependencies")

from pymongo import MongoClient
print("Imported pymongo")

from dotenv import load_dotenv
print("Imported dotenv")

import os
print("Imported os")

from werkzeug.security import generate_password_hash, check_password_hash
print("Imported werkzeug.security")

from bson.objectid import ObjectId
print("Imported bson.objectid")

from datetime import datetime, timedelta
print("Imported datetime")

app = Flask(__name__)
app.secret_key = 'your-very-long-and-random-secret-key-123456'  # Updated for security
print("Flask app initialized")

# Load MongoDB URI from .env
try:
    load_dotenv()
    print("Loaded .env file")
    mongo_uri = os.getenv("MONGO_URI")
    if not mongo_uri:
        raise ValueError("MONGO_URI not found in .env file")
    print(f"MONGO_URI: {mongo_uri}")
except Exception as e:
    print(f"Error loading MONGO_URI: {e}")
    exit(1)

# Connect to MongoDB
try:
    client = MongoClient(mongo_uri)
    client.server_info()  # Test the connection
    print("Connected to MongoDB successfully")
except Exception as e:
    print(f"Failed to connect to MongoDB: {e}")
    exit(1)

# Access database and collections
try:
    db = client.library
    users_collection = db.users
    books_collection = db.books
    conference_rooms_collection = db.conference_rooms
    print("Accessed database and collections")
except Exception as e:
    print(f"Error accessing database/collections: {e}")
    exit(1)

# Helper function to check and cancel expired book reservations (48 hours)
def cancel_expired_book_reservations():
    print("Checking for expired book reservations...")
    expiration_threshold = datetime.utcnow() - timedelta(hours=48)
    expired_books = books_collection.find({
        "status": "reserved",
        "reserved_at": {"$lt": expiration_threshold}
    })
    for book in expired_books:
        print(f"Cancelling expired reservation for book: {book['title']}")
        books_collection.update_one(
            {"_id": book["_id"]},
            {"$set": {"status": "available"}, "$unset": {"reserved_by": "", "reserved_at": ""}}
        )

# Helper function to calculate remaining time for book reservation (48 hours)
def calculate_remaining_time_book_reservation(reserved_at):
    if not reserved_at:
        return {"has_expired": True, "remaining_str": "Expired"}
    reservation_end = reserved_at + timedelta(hours=48)
    now = datetime.utcnow()
    remaining_time = reservation_end - now
    if remaining_time.total_seconds() <= 0:
        return {"has_expired": True, "remaining_str": "Expired"}
    days = remaining_time.days
    hours, remainder = divmod(remaining_time.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return {
        "has_expired": False,
        "remaining_str": f"{days} days, {hours} hours, {minutes} minutes"
    }

# Helper function to calculate remaining time and late fees for borrowing (7 days)
def calculate_borrowing_info(borrowed_at):
    if not borrowed_at:
        return {"has_expired": True, "remaining_str": "Expired", "late_fee": 0}
    borrowing_end = borrowed_at + timedelta(days=7)
    now = datetime.utcnow()
    remaining_time = borrowing_end - now
    if remaining_time.total_seconds() > 0:
        # Not expired yet
        days = remaining_time.days
        hours, remainder = divmod(remaining_time.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        return {
            "has_expired": False,
            "remaining_str": f"{days} days, {hours} hours, {minutes} minutes",
            "late_fee": 0
        }
    else:
        # Expired: calculate late fee
        overdue_time = now - borrowing_end
        overdue_days = overdue_time.days + (1 if overdue_time.seconds > 0 else 0)  # Round up to the next day
        late_fee = overdue_days * 25  # 25 pesos per day
        return {
            "has_expired": True,
            "remaining_str": "Overdue",
            "late_fee": late_fee
        }

# Helper function to clean up expired conference room reservations
def clean_expired_conference_reservations():
    print("Cleaning up expired conference room reservations...")
    now = datetime.utcnow()
    rooms = conference_rooms_collection.find()
    for room in rooms:
        updated_reservations = [
            res for res in room["reservations"]
            if res["end_time"] > now
        ]
        conference_rooms_collection.update_one(
            {"_id": room["_id"]},
            {"$set": {"reservations": updated_reservations}}
        )

# Helper function to check if a student can reserve a conference room
def can_student_reserve_conference_room(student_id):
    rooms = conference_rooms_collection.find()
    for room in rooms:
        for res in room["reservations"]:
            if res["reserved_by"] == student_id:
                return False  # Student already has a reservation
    return True

# Helper function to find the next available time slot for a conference room
def find_next_available_slot(room, reservation_date_str):
    reservation_date = datetime.strptime(reservation_date_str, "%Y-%m-%d").date()
    reservations = sorted(
        [res for res in room["reservations"] if res["date"] == reservation_date_str],
        key=lambda x: x["start_time"]
    )
    
    # Start time must be between 8:00 AM and 6:00 PM
    start_of_day = datetime.combine(reservation_date, datetime.min.time()) + timedelta(hours=8)
    end_of_day = datetime.combine(reservation_date, datetime.min.time()) + timedelta(hours=18)
    
    # If no reservations, start at 8:00 AM
    if not reservations:
        return start_of_day
    
    # Find the next available slot
    last_end_time = start_of_day
    for res in reservations:
        if res["start_time"] > last_end_time:
            return last_end_time
        last_end_time = max(last_end_time, res["end_time"])
    
    # If there's space after the last reservation
    if last_end_time < end_of_day:
        # Ensure there's enough time for a 1.5-hour slot
        if (end_of_day - last_end_time).total_seconds() >= 1.5 * 3600:
            return last_end_time
    
    return None  # No available slot

@app.route("/")
def index():
    print("Accessing / route")
    if "user" in session:
        if session["user"].get("is_admin", False):
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    print("Accessing /register route")
    if request.method == "POST":
        IDNumber = request.form["IDNumber"].strip()
        password = request.form["password"].strip()
        confirm_password = request.form["confirm_password"].strip()
        if not IDNumber or not password or not confirm_password:
            flash("All fields are required", "danger")
            return render_template("register.html")
        
        # Check if passwords match
        if password != confirm_password:
            flash("Passwords do not match", "danger")
            return render_template("register.html")
        
        # Check if IDNumber already exists
        existing_user = users_collection.find_one({"IDNumber": IDNumber})
        if existing_user:
            flash("IDNumber already exists", "danger")
            return render_template("register.html")
        
        # Hash the password and create a new student user (not admin)
        hashed_password = generate_password_hash(password)
        users_collection.insert_one({
            "IDNumber": IDNumber,
            "password": hashed_password,
            "is_admin": False
        })
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("login"))
    
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    print("Accessing /login route")
    if request.method == "POST":
        IDNumber = request.form["IDNumber"].strip()
        password = request.form["password"].strip()
        if not IDNumber or not password:
            flash("IDNumber and password are required", "danger")
            return render_template("login.html")
        user = users_collection.find_one({"IDNumber": IDNumber})
        if user and check_password_hash(user["password"], password):
            session["user"] = {
                "IDNumber": user["IDNumber"],
                "is_admin": user.get("is_admin", False)
            }
            flash("Login successful!", "success")
            if session["user"]["is_admin"]:
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("dashboard"))
        flash("Invalid credentials", "danger")
        return render_template("login.html")
    return render_template("login.html")

@app.route("/logout")
def logout():
    print("Accessing /logout route")
    session.pop("user", None)
    flash("Logged out successfully", "success")
    return redirect(url_for("index"))

@app.route("/dashboard")
def dashboard():
    print("Accessing /dashboard route")
    if "user" not in session:
        flash("Please log in to access the dashboard", "danger")
        return redirect(url_for("login"))
    if session["user"].get("is_admin", False):
        return redirect(url_for("admin_dashboard"))
    
    # Check for expired reservations
    cancel_expired_book_reservations()
    clean_expired_conference_reservations()
    
    # Get search query
    search_query = request.args.get("search", "").lower()
    selected_genre = request.args.get("genre", "")
    
    # Fetch suggestions for the choices bar (limit to 5)
    suggestions = []
    if search_query:
        suggestions_cursor = books_collection.find({
            "$or": [
                {"title": {"$regex": search_query, "$options": "i"}},
                {"author": {"$regex": search_query, "$options": "i"}},
                {"genre": {"$regex": search_query, "$options": "i"}}
            ]
        }).limit(5)
        suggestions = list(suggestions_cursor)
    
    # Fetch books based on search query and selected genre
    query = {}
    if search_query:
        query["$or"] = [
            {"title": {"$regex": search_query, "$options": "i"}},
            {"author": {"$regex": search_query, "$options": "i"}},
            {"genre": {"$regex": search_query, "$options": "i"}}
        ]
    if selected_genre:
        query["genre"] = selected_genre
    
    books_cursor = books_collection.find(query) if query else books_collection.find()
    books = list(books_cursor)
    
    # Fetch reserved or borrowed books for the student
    student_books = list(books_collection.find({
        "$or": [
            {"status": "reserved", "reserved_by": session["user"]["IDNumber"]},
            {"status": "borrowed", "reserved_by": session["user"]["IDNumber"]}
        ]
    }))
    
    # Add timing info to each book
    for book in student_books:
        if book["status"] == "reserved":
            book["timing_info"] = calculate_remaining_time_book_reservation(book.get("reserved_at"))
        elif book["status"] == "borrowed":
            book["timing_info"] = calculate_borrowing_info(book.get("borrowed_at"))
    
    # Fetch all unique genres for the dropdown
    genres = sorted(list(set(book.get("genre", "Unknown") for book in books_collection.find())))
    
    # Fetch conference rooms and their reservations
    conference_rooms = list(conference_rooms_collection.find())
    now = datetime.utcnow()
    tomorrow = (now + timedelta(days=1)).date()
    tomorrow_str = tomorrow.strftime("%Y-%m-%d")
    
    # Prepare conference room statuses
    conference_room_statuses = []
    for room in conference_rooms:
        status = {
            "room_id": str(room["_id"]),
            "room_name": room["room_name"],
            "current_status": "Available",
            "reservations": []
        }
        
        # Check current and upcoming reservations
        for res in room["reservations"]:
            start_time = res["start_time"]
            end_time = res["end_time"]
            res_date = datetime.strptime(res["date"], "%Y-%m-%d").date()
            
            # Format the time for display
            start_str = start_time.strftime("%I:%M %p").lstrip("0")
            end_str = end_time.strftime("%I:%M %p").lstrip("0")
            date_str = start_time.strftime("%B %d, %Y")
            
            if now >= start_time and now <= end_time:
                status["current_status"] = f"Currently in use by {res['reserved_by']} until {end_str}"
            elif res_date == tomorrow:
                status["reservations"].append({
                    "reserved_by": res["reserved_by"],
                    "time_frame": f"{date_str}, {start_str} - {end_str}",
                    "start_time": start_time,
                    "end_time": end_time
                })
        
        # Sort reservations by start time
        status["reservations"].sort(key=lambda x: x["start_time"])
        
        # Check if the student can reserve this room for tomorrow
        can_reserve = can_student_reserve_conference_room(session["user"]["IDNumber"])
        if can_reserve:
            next_slot = find_next_available_slot(room, tomorrow_str)
            if next_slot:
                status["next_available_slot"] = next_slot
                status["next_slot_str"] = next_slot.strftime("%I:%M %p").lstrip("0")
                status["next_slot_end"] = (next_slot + timedelta(hours=1, minutes=30)).strftime("%I:%M %p").lstrip("0")
        
        conference_room_statuses.append(status)
    
    return render_template("dashboard.html", 
                         books=books, 
                         student_books=student_books, 
                         user=session["user"], 
                         suggestions=suggestions, 
                         search_query=search_query, 
                         selected_genre=selected_genre, 
                         genres=genres,
                         conference_room_statuses=conference_room_statuses,
                         tomorrow_date=tomorrow_str)

@app.route("/reserve/<book_id>", methods=["POST"])
def reserve_book(book_id):
    print(f"Accessing /reserve/{book_id} route")
    if "user" not in session:
        flash("Please log in to reserve a book", "danger")
        return redirect(url_for("login"))
    if session["user"].get("is_admin", False):
        return redirect(url_for("admin_dashboard"))
    
    # Check for expired reservations
    cancel_expired_book_reservations()
    
    # Check if the student already has a reserved or borrowed book
    existing_book = books_collection.find_one({
        "$or": [
            {"status": "reserved", "reserved_by": session["user"]["IDNumber"]},
            {"status": "borrowed", "reserved_by": session["user"]["IDNumber"]}
        ]
    })
    if existing_book:
        # Add timing info for the error display
        if existing_book["status"] == "reserved":
            existing_book["timing_info"] = calculate_remaining_time_book_reservation(existing_book.get("reserved_at"))
        elif existing_book["status"] == "borrowed":
            existing_book["timing_info"] = calculate_borrowing_info(existing_book.get("borrowed_at"))
        books = list(books_collection.find())
        flash("You can only reserve or borrow one book at a time.", "danger")
        return render_template("dashboard.html", 
                             books=books, 
                             student_books=[existing_book], 
                             user=session["user"])
    
    # Reserve the book
    books_collection.update_one(
        {"_id": ObjectId(book_id), "status": "available"},
        {"$set": {
            "status": "reserved",
            "reserved_by": session["user"]["IDNumber"],
            "reserved_at": datetime.utcnow()
        }}
    )
    flash("Book reserved successfully!", "success")
    return redirect(url_for("dashboard"))

@app.route("/cancel_reservation/<book_id>", methods=["POST"])
def cancel_book_reservation(book_id):
    print(f"Accessing /cancel_reservation/{book_id} route")
    if "user" not in session:
        flash("Please log in to cancel a reservation", "danger")
        return redirect(url_for("login"))
    if session["user"].get("is_admin", False):
        return redirect(url_for("admin_dashboard"))
    
    # Cancel the reservation
    books_collection.update_one(
        {"_id": ObjectId(book_id), "reserved_by": session["user"]["IDNumber"], "status": "reserved"},
        {"$set": {"status": "available"}, "$unset": {"reserved_by": "", "reserved_at": ""}}
    )
    flash("Book reservation cancelled successfully!", "success")
    return redirect(url_for("dashboard"))

@app.route("/reserve_conference_room/<room_id>", methods=["POST"])
def reserve_conference_room(room_id):
    print(f"Accessing /reserve_conference_room/{room_id} route")
    if "user" not in session:
        flash("Please log in to reserve a conference room", "danger")
        return redirect(url_for("login"))
    if session["user"].get("is_admin", False):
        return redirect(url_for("admin_dashboard"))
    
    # Clean up expired reservations
    clean_expired_conference_reservations()
    
    # Check if the student already has a conference room reservation
    if not can_student_reserve_conference_room(session["user"]["IDNumber"]):
        flash("You can only reserve one conference room at a time.", "danger")
        return redirect(url_for("dashboard"))
    
    # Get the reservation date (should be tomorrow)
    reservation_date_str = request.form.get("reservation_date")
    if not reservation_date_str:
        flash("Reservation date is required", "danger")
        return redirect(url_for("dashboard"))
    
    reservation_date = datetime.strptime(reservation_date_str, "%Y-%m-%d").date()
    now = datetime.utcnow()
    tomorrow = (now + timedelta(days=1)).date()
    
    # Enforce the "day before" rule
    if reservation_date != tomorrow:
        flash("Conference rooms can only be reserved for the next day.", "danger")
        return redirect(url_for("dashboard"))
    
    # Find the room
    room = conference_rooms_collection.find_one({"_id": ObjectId(room_id)})
    if not room:
        flash("Conference room not found", "danger")
        return redirect(url_for("dashboard"))
    
    # Find the next available slot
    start_time = find_next_available_slot(room, reservation_date_str)
    if not start_time:
        flash(f"No available slots for {room['room_name']} on {reservation_date_str}.", "danger")
        return redirect(url_for("dashboard"))
    
    # Create the reservation
    end_time = start_time + timedelta(hours=1, minutes=30)
    reservation = {
        "reserved_by": session["user"]["IDNumber"],
        "date": reservation_date_str,
        "start_time": start_time,
        "end_time": end_time
    }
    
    # Add the reservation to the room
    conference_rooms_collection.update_one(
        {"_id": ObjectId(room_id)},
        {"$push": {"reservations": reservation}}
    )
    
    flash(f"Successfully reserved {room['room_name']} for {reservation_date_str} from {start_time.strftime('%I:%M %p').lstrip('0')} to {end_time.strftime('%I:%M %p').lstrip('0')}.", "success")
    return redirect(url_for("dashboard"))

@app.route("/cancel_conference_reservation/<room_id>", methods=["POST"])
def cancel_conference_reservation(room_id):
    print(f"Accessing /cancel_conference_reservation/{room_id} route")
    if "user" not in session:
        flash("Please log in to cancel a reservation", "danger")
        return redirect(url_for("login"))
    if session["user"].get("is_admin", False):
        return redirect(url_for("admin_dashboard"))
    
    # Find the room
    room = conference_rooms_collection.find_one({"_id": ObjectId(room_id)})
    if not room:
        flash("Conference room not found", "danger")
        return redirect(url_for("dashboard"))
    
    # Find and remove the student's reservation
    updated_reservations = [
        res for res in room["reservations"]
        if res["reserved_by"] != session["user"]["IDNumber"]
    ]
    
    conference_rooms_collection.update_one(
        {"_id": ObjectId(room_id)},
        {"$set": {"reservations": updated_reservations}}
    )
    
    flash(f"Successfully cancelled your reservation for {room['room_name']}.", "success")
    return redirect(url_for("dashboard"))

@app.route("/admin/dashboard")
def admin_dashboard():
    print("Accessing /admin/dashboard route")
    if "user" not in session or not session["user"].get("is_admin", False):
        flash("You must be an admin to access this page", "danger")
        return redirect(url_for("login"))
    
    # Check for expired reservations
    cancel_expired_book_reservations()
    clean_expired_conference_reservations()
    
    # Get the active tab from the query parameter (default to 'manage-books')
    active_tab = request.args.get("tab", "manage-books")
    
    # Fetch all books
    books = list(books_collection.find())
    
    # Fetch all students (users with is_admin: False)
    students = list(users_collection.find({"is_admin": False}))
    
    # Fetch all reserved or borrowed books
    active_books = list(books_collection.find({"status": {"$in": ["reserved", "borrowed"]}}))
    
    # Add timing info to each book
    for book in active_books:
        if book["status"] == "reserved":
            book["timing_info"] = calculate_remaining_time_book_reservation(book.get("reserved_at"))
        elif book["status"] == "borrowed":
            book["timing_info"] = calculate_borrowing_info(book.get("borrowed_at"))
    
    # Fetch conference rooms and their reservations
    conference_rooms = list(conference_rooms_collection.find())
    now = datetime.utcnow()
    conference_room_statuses = []
    for room in conference_rooms:
        status = {
            "room_id": str(room["_id"]),
            "room_name": room["room_name"],
            "current_status": "Available",
            "reservations": []
        }
        
        # Check current and upcoming reservations
        for res in room["reservations"]:
            start_time = res["start_time"]
            end_time = res["end_time"]
            
            # Format the time for display
            start_str = start_time.strftime("%I:%M %p").lstrip("0")
            end_str = end_time.strftime("%I:%M %p").lstrip("0")
            date_str = start_time.strftime("%B %d, %Y")
            
            if now >= start_time and now <= end_time:
                status["current_status"] = f"Currently in use by {res['reserved_by']} until {end_str}"
            else:
                status["reservations"].append({
                    "reserved_by": res["reserved_by"],
                    "time_frame": f"{date_str}, {start_str} - {end_str}",
                    "start_time": start_time,
                    "end_time": end_time
                })
        
        # Sort reservations by start time
        status["reservations"].sort(key=lambda x: x["start_time"])
        
        conference_room_statuses.append(status)
    
    return render_template("admin_dashboard.html", 
                         books=books, 
                         students=students, 
                         active_books=active_books, 
                         conference_room_statuses=conference_room_statuses,
                         user=session["user"], 
                         active_tab=active_tab)

@app.route("/admin/mark_borrowed/<book_id>", methods=["POST"])
def mark_borrowed(book_id):
    print(f"Accessing /admin/mark_borrowed/{book_id} route")
    if "user" not in session or not session["user"].get("is_admin", False):
        flash("You must be an admin to access this page", "danger")
        return redirect(url_for("login"))
    
    # Mark the book as borrowed
    books_collection.update_one(
        {"_id": ObjectId(book_id), "status": "reserved"},
        {"$set": {
            "status": "borrowed",
            "borrowed_at": datetime.utcnow()
        }}
    )
    flash("Book marked as borrowed!", "success")
    return redirect(url_for("admin_dashboard", tab="active-books"))

@app.route("/admin/mark_returned/<book_id>", methods=["POST"])
def mark_returned(book_id):
    print(f"Accessing /admin/mark_returned/{book_id} route")
    if "user" not in session or not session["user"].get("is_admin", False):
        flash("You must be an admin to access this page", "danger")
        return redirect(url_for("login"))
    
    # Mark the book as returned
    books_collection.update_one(
        {"_id": ObjectId(book_id), "status": "borrowed"},
        {"$set": {
            "status": "available",
            "returned_at": datetime.utcnow()
        }, "$unset": {"reserved_by": "", "reserved_at": "", "borrowed_at": ""}}
    )
    flash("Book marked as returned!", "success")
    return redirect(url_for("admin_dashboard", tab="active-books"))

@app.route("/admin/add_book", methods=["GET", "POST"])
def add_book():
    print("Accessing /admin/add_book route")
    if "user" not in session or not session["user"].get("is_admin", False):
        flash("You must be an admin to access this page", "danger")
        return redirect(url_for("login"))
    
    # Fetch all unique genres
    genres = sorted(list(set(book.get("genre", "Unknown") for book in books_collection.find())))
    
    if request.method == "POST":
        title = request.form["title"].strip()
        author = request.form["author"].strip()
        genre = request.form["genre"].strip()
        if not title or not author or not genre:
            flash("Title, Author, and Genre are required", "danger")
            return render_template("add_book.html", genres=genres)
        books_collection.insert_one({
            "title": title,
            "author": author,
            "genre": genre,
            "status": "available"
        })
        flash("Book added successfully!", "success")
        return redirect(url_for("admin_dashboard", tab="manage-books"))
    
    return render_template("add_book.html", genres=genres)

@app.route("/admin/edit_book/<book_id>", methods=["GET", "POST"])
def edit_book(book_id):
    print(f"Accessing /admin/edit_book/{book_id} route")
    if "user" not in session or not session["user"].get("is_admin", False):
        flash("You must be an admin to access this page", "danger")
        return redirect(url_for("login"))
    
    book = books_collection.find_one({"_id": ObjectId(book_id)})
    if not book:
        flash("Book not found", "danger")
        return redirect(url_for("admin_dashboard", tab="manage-books"))
    
    # Fetch all unique genres
    genres = sorted(list(set(book.get("genre", "Unknown") for book in books_collection.find())))
    
    if request.method == "POST":
        title = request.form["title"].strip()
        author = request.form["author"].strip()
        genre = request.form["genre"].strip()
        if not title or not author or not genre:
            flash("Title, Author, and Genre are required", "danger")
            return render_template("edit_book.html", book=book, genres=genres)
        books_collection.update_one(
            {"_id": ObjectId(book_id)},
            {"$set": {"title": title, "author": author, "genre": genre}}
        )
        flash("Book updated successfully!", "success")
        return redirect(url_for("admin_dashboard", tab="manage-books"))
    
    return render_template("edit_book.html", book=book, genres=genres)

@app.route("/admin/delete_book/<book_id>", methods=["POST"])
def delete_book(book_id):
    print(f"Accessing /admin/delete_book/{book_id} route")
    if "user" not in session or not session["user"].get("is_admin", False):
        flash("You must be an admin to access this page", "danger")
        return redirect(url_for("login"))
    
    books_collection.delete_one({"_id": ObjectId(book_id)})
    flash("Book deleted successfully!", "success")
    return redirect(url_for("admin_dashboard", tab="manage-books"))

@app.route("/admin/cancel_conference_reservation/<room_id>/<reserved_by>", methods=["POST"])
def admin_cancel_conference_reservation(room_id, reserved_by):
    print(f"Accessing /admin/cancel_conference_reservation/{room_id}/{reserved_by} route")
    if "user" not in session or not session["user"].get("is_admin", False):
        flash("You must be an admin to access this page", "danger")
        return redirect(url_for("login"))
    
    # Find the room
    room = conference_rooms_collection.find_one({"_id": ObjectId(room_id)})
    if not room:
        flash("Conference room not found", "danger")
        return redirect(url_for("admin_dashboard", tab="conference-rooms"))
    
    # Find and remove the reservation by the specified student
    updated_reservations = [
        res for res in room["reservations"]
        if res["reserved_by"] != reserved_by
    ]
    
    conference_rooms_collection.update_one(
        {"_id": ObjectId(room_id)},
        {"$set": {"reservations": updated_reservations}}
    )
    
    flash(f"Successfully cancelled reservation for {room['room_name']} by {reserved_by}.", "success")
    return redirect(url_for("admin_dashboard", tab="conference-rooms"))

@app.route("/admin/add_conference_reservation/<room_id>", methods=["GET", "POST"])
def add_conference_reservation(room_id):
    print(f"Accessing /admin/add_conference_reservation/{room_id} route")
    if "user" not in session or not session["user"].get("is_admin", False):
        flash("You must be an admin to access this page", "danger")
        return redirect(url_for("login"))
    
    room = conference_rooms_collection.find_one({"_id": ObjectId(room_id)})
    if not room:
        flash("Conference room not found", "danger")
        return redirect(url_for("admin_dashboard", tab="conference-rooms"))
    
    # Fetch all students for the dropdown
    students = list(users_collection.find({"is_admin": False}))
    
    if request.method == "POST":
        student_id = request.form.get("student_id")
        reservation_date_str = request.form.get("reservation_date")
        start_time_str = request.form.get("start_time")
        
        if not student_id or not reservation_date_str or not start_time_str:
            flash("All fields are required", "danger")
            return render_template("add_conference_reservation.html", room=room, students=students)
        
        # Validate the student
        student = users_collection.find_one({"IDNumber": student_id, "is_admin": False})
        if not student:
            flash("Invalid student ID", "danger")
            return render_template("add_conference_reservation.html", room=room, students=students)
        
        # Check if the student already has a reservation
        if not can_student_reserve_conference_room(student_id):
            flash(f"Student {student_id} already has a conference room reservation.", "danger")
            return render_template("add_conference_reservation.html", room=room, students=students)
        
        # Parse the date and time
        try:
            reservation_date = datetime.strptime(reservation_date_str, "%Y-%m-%d").date()
            start_time = datetime.strptime(f"{reservation_date_str} {start_time_str}", "%Y-%m-%d %H:%M")
            end_time = start_time + timedelta(hours=1, minutes=30)
        except ValueError:
            flash("Invalid date or time format", "danger")
            return render_template("add_conference_reservation.html", room=room, students=students)
        
        # Check if the time slot is available
        reservations = [res for res in room["reservations"] if res["date"] == reservation_date_str]
        for res in reservations:
            res_start = res["start_time"]
            res_end = res["end_time"]
            if (start_time < res_end and end_time > res_start):
                flash("This time slot is already reserved", "danger")
                return render_template("add_conference_reservation.html", room=room, students=students)
        
        # Check if the time is between 8:00 AM and 6:00 PM
        start_of_day = datetime.combine(reservation_date, datetime.min.time()) + timedelta(hours=8)
        end_of_day = datetime.combine(reservation_date, datetime.min.time()) + timedelta(hours=18)
        if start_time < start_of_day or end_time > end_of_day:
            flash("Reservations must be between 8:00 AM and 6:00 PM", "danger")
            return render_template("add_conference_reservation.html", room=room, students=students)
        
        # Create the reservation
        reservation = {
            "reserved_by": student_id,
            "date": reservation_date_str,
            "start_time": start_time,
            "end_time": end_time
        }
        
        # Add the reservation to the room
        conference_rooms_collection.update_one(
            {"_id": ObjectId(room_id)},
            {"$push": {"reservations": reservation}}
        )
        
        flash(f"Successfully reserved {room['room_name']} for {student_id} on {reservation_date_str} from {start_time.strftime('%I:%M %p').lstrip('0')} to {end_time.strftime('%I:%M %p').lstrip('0')}.", "success")
        return redirect(url_for("admin_dashboard", tab="conference-rooms"))
    
    return render_template("add_conference_reservation.html", room=room, students=students)

if __name__ == "__main__":
    print("Starting Flask app...")
    app.run(debug=True)