import sqlite3


def login(username, password):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    query = "SELECT * FROM users WHERE username = '" + username + "' AND password = '" + password + "'"
    cur.execute(query)
    return cur.fetchone() is not None
