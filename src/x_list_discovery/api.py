"""Read-only twitterapi.io client. Importing this module never makes a request."""
from __future__ import annotations

import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from .models import identity, profile


class APIError(RuntimeError):
    pass


def list_id(value):
    value = str(value).strip()
    if re.fullmatch(r"[0-9]{1,30}", value):
        return value
    url = urlparse(value)
    if url.scheme not in {"http", "https"} or url.netloc.lower() not in {
        "x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"
    }:
        raise ValueError("Provide an X List URL or numeric List ID")
    match = re.fullmatch(r"/(?:i/)?lists/([0-9]{1,30})/?", url.path)
    if not match:
        raise ValueError("Expected a List URL such as https://x.com/i/lists/123456789")
    return match.group(1)


class TwitterAPI:
    def __init__(self, key=None, env_file=None, key_env="TWITTERAPI_IO_KEY", request=None, sleep=time.sleep):
        if key is None:
            load_dotenv(env_file or Path.cwd() / ".env", override=False)
            key = os.environ.get(key_env)
            if not key and key_env == "TWITTERAPI_IO_KEY":
                key = os.environ.get("API")  # Compatibility with existing local snapshots/workflows.
        if not key:
            raise APIError("Missing API key: configure " + key_env + " in the environment or a dotenv file")
        self.headers = {"X-API-Key": key}
        self.request = request
        self.local = threading.local()
        self.sleep = sleep

    def get(self, path, params):
        if self.request is None:
            if not hasattr(self.local, "session"):
                self.local.session = requests.Session()
            request = self.local.session.get
        else:
            request = self.request
        for attempt in range(3):
            try:
                response = request("https://api.twitterapi.io" + path, headers=self.headers, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or data.get("status") not in (None, "success"):
                    raise APIError("API business error at " + path)
                return data
            except (requests.RequestException, ValueError) as error:
                status = getattr(getattr(error, "response", None), "status_code", None)
                if attempt == 2 or status is not None and 400 <= status < 500 and status != 429:
                    raise APIError("Request failed at {} ({})".format(path, status or type(error).__name__)) from None
                self.sleep(0.5 * 2 ** attempt)

    def user(self, username):
        data = self.get("/twitter/user/info", {"userName": username}).get("data")
        if not isinstance(data, dict):
            raise APIError("Unexpected user/info shape")
        if data.get("unavailable"):
            return {"userName": username, "protected": True, "following_count": None}
        return profile(data)

    def _profiles(self, path, key, params, max_pages):
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        result, ids, cursors, cursor = [], set(), set(), None
        for _ in range(max_pages):
            query = {**params, **({"cursor": cursor} if cursor else {})}
            payload = self.get(path, query)
            if not isinstance(payload.get(key), list) or not isinstance(payload.get("has_next_page"), bool):
                raise APIError("Unexpected " + key + " shape; not a completed empty list")
            for raw in payload[key]:
                try:
                    user = profile(raw)
                except (TypeError, ValueError, AttributeError):
                    raise APIError("Invalid profile in " + key + " page") from None
                uid = identity(user)
                if uid not in ids:
                    ids.add(uid)
                    result.append(user)
            if not payload["has_next_page"]:
                return result
            cursor = payload.get("next_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise APIError("Incomplete " + key + ": missing or repeated cursor")
            cursors.add(cursor)
        raise APIError(key + " page limit reached; result is incomplete")

    def followings(self, username, max_pages=400):
        return self._profiles("/twitter/user/followings", "followings",
                              {"userName": username, "pageSize": 200}, max_pages)

    def list_members(self, value, max_pages=1000):
        # Official schema: top-level members[], has_next_page, next_cursor.
        return self._profiles("/twitter/list/members", "members", {"list_id": list_id(value)}, max_pages)
