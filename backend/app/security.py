# 安全工具：密码哈希、密码强度校验、强密码生成（消灭默认弱口令）
import re
import secrets
import string

import bcrypt

# 内置常见弱口令表（top 常见弱口令子集），命中即拒绝
WEAK_PASSWORDS = {
    "123456", "123456789", "12345678", "1234567890", "12345", "1234567", "111111", "123123",
    "000000", "888888", "666666", "654321", "121212", "112233", "123321", "159753", "147258",
    "password", "passw0rd", "password1", "admin", "admin123", "root", "root123", "toor",
    "qwerty", "qwerty123", "qwertyuiop", "1q2w3e4r", "1qaz2wsx", "zaq12wsx", "asdfgh", "asdf1234",
    "abc123", "abcd1234", "a1b2c3d4", "iloveyou", "welcome", "welcome1", "letmein", "monkey",
    "dragon", "sunshine", "princess", "football", "baseball", "master", "superman", "trustno1",
    "michael", "shadow", "ashley", "qazwsx", "123qwe", "1234qwer", "test123", "user123", "guest",
    "changeme", "secret", "login", "pass", "temp123", "p@ssw0rd", "p@ssword", "admin@123",
    "woaini", "woaini1314", "5201314", "520520", "1314520", "wang123", "zhang123", "li123456",
    "88888888", "11111111", "00000000", "aaaaaa", "aaaaaaaa", "asdasd", "asd123", "zxcvbnm",
    "qweasdzxc", "1qazxsw2", "qwe123456", "123456abc", "abc123456", "a123456", "a1234567",
    "123abc", "test1234", "demo1234", "root1234", "admin1234", "pass1234", "passw0rd1",
}


def hash_password(password: str) -> str:
    """bcrypt 哈希，cost=12"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def validate_password_strength(pwd: str) -> None:
    """密码强度校验：≥10 位，含大小写/数字/符号中至少三类，且不在弱口令表。不合规抛 ValueError。"""
    from .utils import BizError

    if not pwd or len(pwd) < 10:
        raise BizError(1000, "密码强度不足：长度至少 10 位")
    if pwd.lower() in WEAK_PASSWORDS:
        raise BizError(1000, "密码强度不足：该密码过于常见，请更换")
    kinds = 0
    if re.search(r"[a-z]", pwd):
        kinds += 1
    if re.search(r"[A-Z]", pwd):
        kinds += 1
    if re.search(r"[0-9]", pwd):
        kinds += 1
    if re.search(r"[^a-zA-Z0-9]", pwd):
        kinds += 1
    if kinds < 3:
        raise BizError(1000, "密码强度不足：需包含大写字母、小写字母、数字、符号中的至少三类")


def generate_strong_password(length: int = 16) -> str:
    """生成满足强度要求的随机密码（含四类字符）"""
    lowers = string.ascii_lowercase
    uppers = string.ascii_uppercase
    digits = string.digits
    symbols = "!@#$%^&*-_=+"
    alphabet = lowers + uppers + digits + symbols
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        # 确保四类齐全（generate 出来后校验，不满足则重来）
        if (any(c in lowers for c in pwd) and any(c in uppers for c in pwd)
                and any(c in digits for c in pwd) and any(c in symbols for c in pwd)
                and pwd.lower() not in WEAK_PASSWORDS):
            return pwd
