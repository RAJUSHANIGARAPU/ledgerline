MIN_LENGTH = 12
UNUSABLE = "!"


def check_new_password(password: str, confirm: str, email: str) -> str | None:
    if len(password) < MIN_LENGTH:
        return f"Password must be at least {MIN_LENGTH} characters."
    if password != confirm:
        return "The two passwords do not match."
    if len(set(password)) == 1:
        return "Password cannot be one repeated character."
    if password.strip().lower() == email.strip().lower():
        return "Password cannot be your email address."
    return None
