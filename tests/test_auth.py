from app.auth import hash_password, verify_password, validate_credentials


def test_hash_and_verify_roundtrip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong password", h)


def test_hash_is_salted_so_same_password_differs():
    assert hash_password("samepw12") != hash_password("samepw12")


def test_verify_handles_garbage_hash():
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False


def test_validate_credentials():
    assert validate_credentials("a@b.co", "longenough") is None
    assert validate_credentials("notanemail", "longenough") is not None   # bad email
    assert validate_credentials("a@b.co", "short") is not None            # too-short password
