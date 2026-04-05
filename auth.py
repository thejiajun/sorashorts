import os
import functools
from flask import request, jsonify, g
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

def get_supabase():
    if SUPABASE_URL and SUPABASE_KEY:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    return None

def require_auth(f):
    """Decorator: validate Supabase JWT and set g.user_id"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Authentication required"}), 401

        token = auth_header.split(" ", 1)[1]
        sb = get_supabase()
        if not sb:
            return jsonify({"error": "Auth service unavailable"}), 503

        try:
            user_resp = sb.auth.get_user(token)
            if not user_resp or not user_resp.user:
                return jsonify({"error": "Invalid token"}), 401
            g.user_id = user_resp.user.id
            g.user_email = user_resp.user.email
            g.auth_token = token
        except Exception:
            return jsonify({"error": "Invalid or expired token"}), 401

        return f(*args, **kwargs)
    return decorated


def optional_auth(f):
    """Decorator: try to authenticate but don't require it. Sets g.user_id or None."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        g.user_id = None
        g.user_email = None
        g.auth_token = None

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
            sb = get_supabase()
            if sb:
                try:
                    user_resp = sb.auth.get_user(token)
                    if user_resp and user_resp.user:
                        g.user_id = user_resp.user.id
                        g.user_email = user_resp.user.email
                        g.auth_token = token
                except Exception:
                    pass

        return f(*args, **kwargs)
    return decorated
