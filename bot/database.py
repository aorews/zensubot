import os
from datetime import datetime, timedelta
from time import sleep

from constants import JOB_DAYS_DURATION

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")
CONNECTION = psycopg2.connect(DATABASE_URL, sslmode="require")


def db_query(line, fetching=True):
    global CONNECTION
    while True:
        try:
            with CONNECTION:
                with CONNECTION.cursor() as cur:
                    cur.execute(line)
                    if fetching:
                        data = cur.fetchall()
                        return data
                    return None
        except Exception as e:
            print(f"DB connection issue: {e}")
            CONNECTION = psycopg2.connect(DATABASE_URL, sslmode="require")
            sleep(1)

def clean_data(job):
    def cleaner(context):
        db_query(
            f"""delete from plus_data where date_part('day', now() - created) >= {JOB_DAYS_DURATION};
                delete from jobs_updates where job_id in (select id from jobs where type = 0 and date_part('day', now() - created) >= {JOB_DAYS_DURATION});
                delete from jobs where (type = 0 or coalesce(order_number, 0) = 0) and date_part('day', now() - created) >= {JOB_DAYS_DURATION};""", False
        )

    job.run_repeating(
        callback=cleaner, interval=timedelta(days=JOB_DAYS_DURATION), first=datetime(2021, 1, 1)
    )

def get_effective_job(chat_id):
    """
    Input is the chat_id where the sticker is posted.
    Output is chat_id and message_id where the job is posted as it could be in the channel.
    """
    data = db_query(f"""
        SELECT message_id, chat_id, child FROM jobs LEFT OUTER JOIN chats_connection ON jobs.chat_id=chats_connection.parent
        WHERE id IN (
            SELECT MAX(id) FROM jobs GROUP BY type
        );
        """)
    for (message_id, effective_chat_id, child) in data:
        if chat_id == child or chat_id == effective_chat_id:
            return (effective_chat_id, message_id)
