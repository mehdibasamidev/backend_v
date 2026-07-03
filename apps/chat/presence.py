
import redis
import os

redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "redis"),
    port=6379,
    db=0
)

ONLINE_USERS_KEY = "online_users"


def add_online_user(user_id):
    redis_client.sadd(ONLINE_USERS_KEY, str(user_id))


def remove_online_user(user_id):
    redis_client.srem(ONLINE_USERS_KEY, str(user_id))


def is_user_online(user_id):
    return redis_client.sismember(ONLINE_USERS_KEY, str(user_id))


def get_online_users():
    users = redis_client.smembers(ONLINE_USERS_KEY)
    return [u.decode("utf-8") for u in users]
