from werkzeug.security import generate_password_hash

# Set your desired password
new_password = "123456"  # Replace with your desired password

# Generate the hashed password
hashed_password = generate_password_hash(new_password)
print(f"New password: {new_password}")
print(f"Hashed password: {hashed_password}")