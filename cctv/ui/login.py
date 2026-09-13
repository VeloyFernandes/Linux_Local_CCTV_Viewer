"""Monitor login dialog: first-run sign-up, sign-in and password change."""
from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout,
                               QLabel, QLineEdit, QVBoxLayout)

from ..config import AppSettings, check_password, hash_password

_MODE_TITLES = {
    "create": "Create Login",
    "login": "Log In",
    "change": "Change Password",
}


class LoginDialog(QDialog):
    """Signs the user in, or creates / changes the stored login.

    Modes:
    - ``create`` — no login exists yet; register a username + password
    - ``login``  — verify the stored password
    - ``change`` — replace the password after verifying the current one
    """

    def __init__(self, settings: AppSettings, mode: str = "login", parent=None):
        super().__init__(parent)
        self._settings = settings
        self._mode = mode if mode in _MODE_TITLES else "login"
        self.setWindowTitle(_MODE_TITLES[self._mode])
        self.setMinimumWidth(380)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        note = QLabel()
        note.setWordWrap(True)
        if self._mode == "create":
            note.setText("No login is set for this monitor yet.\n"
                         "Pick a username and password to protect it.")
        elif self._mode == "change":
            note.setText(f"Change the password for "
                         f"“{self._settings.login_username}”.")
        else:
            note.setText(f"Log in as “{self._settings.login_username}”.")
        note.setObjectName("loginNote")
        layout.addWidget(note)

        form = QFormLayout()
        self.user_edit = QLineEdit()
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.current_edit = QLineEdit()
        self.current_edit.setEchoMode(QLineEdit.Password)

        if self._mode == "create":
            form.addRow("Username:", self.user_edit)
            form.addRow("Password:", self.pass_edit)
        elif self._mode == "change":
            form.addRow("Current password:", self.current_edit)
            form.addRow("New password:", self.pass_edit)
        else:
            self.user_edit.setText(self._settings.login_username)
            self.user_edit.hide()
            form.addRow("Password:", self.pass_edit)
        layout.addLayout(form)

        self._error = QLabel("")
        self._error.setObjectName("loginError")
        self._error.setWordWrap(True)
        self._error.hide()
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText(
            {"create": "Create Login", "login": "Log In",
             "change": "Change Password"}[self._mode])
        layout.addWidget(buttons)

        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        self.pass_edit.returnPressed.connect(self._validate)

    def _fail(self, message: str) -> None:
        self._error.setText(message)
        self._error.show()

    def _validate(self) -> None:
        user = self.user_edit.text().strip()
        password = self.pass_edit.text()

        if self._mode == "create":
            if not user:
                self._fail("Choose a username.")
                return
            if len(password) < 4:
                self._fail("Use at least 4 characters for the password.")
                return
            self._settings.login_username = user
            self._settings.login_pass_hash = hash_password(password)
            self._settings.save()
        elif self._mode == "change":
            if not check_password(self.current_edit.text(),
                                  self._settings.login_pass_hash):
                self._fail("The current password is not correct.")
                return
            if len(password) < 4:
                self._fail("Use at least 4 characters for the new password.")
                return
            self._settings.login_pass_hash = hash_password(password)
            self._settings.save()
        else:
            if not check_password(password, self._settings.login_pass_hash):
                self._fail("Wrong password.")
                return
        self.accept()
