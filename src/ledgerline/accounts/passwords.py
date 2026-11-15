from ledgerline.i18n import translate

MIN_LENGTH = 12
UNUSABLE = "!"


def password_problem(password: str, confirm: str, email: str) -> tuple[str, dict] | None:
    """The first rule broken, as a message key and its parameters; None when acceptable."""
    if len(password) < MIN_LENGTH:
        return "password.too_short", {"min": MIN_LENGTH}
    if password != confirm:
        return "password.mismatch", {}
    if len(set(password)) == 1:
        return "password.repeated", {}
    if password.strip().lower() == email.strip().lower():
        return "password.is_email", {}
    return None


def check_new_password(password: str, confirm: str, email: str, lang: str = "en") -> str | None:
    problem = password_problem(password, confirm, email)
    return translate(lang, problem[0], **problem[1]) if problem else None
