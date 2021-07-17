import logging
import re
from datetime import datetime, timedelta

from telegram import ParseMode
from telegram.error import BadRequest

from database import db_query
from responses import Responses

logger = logging.getLogger(__name__)

def bot_message_to_chat(context, chat_id, text, delete = 0, reply_to_message = None, parse_mode = None):
    posted_message = context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_to_message_id=reply_to_message,
        parse_mode = parse_mode
    )
    if delete != 0:
        context.job_queue.run_once(
            delete_message,
            delete,
            context=[posted_message.message_id, chat_id],
        )

def delete_message(context) -> None:
    job = context.job.context
    context.bot.delete_message(chat_id=job[1], message_id=job[0])


def fill_template(text, n, start_date = datetime.now()) -> str:
    UTC_PLUS = 3
    text = re.sub('([#№])N', f"\g<1>{n}", text, flags=re.I)
    for day in range(5):
        date = start_date + timedelta(days=day, hours=UTC_PLUS)
        open, close = ('','') if datetime.now() - date < timedelta(hours = 24 - UTC_PLUS) else ('<b><s>', '</s></b>')
        date = date.strftime("%d.%m.%Y")
        text = re.sub(f"{day+1} [-–—] NN.NN.NNNN", f"{open}{day+1} — {date}{close}", text, flags=re.I)
    return text


def minutes_to_hours(minutes, mode = 0):
    if mode == 0:
        return f"{minutes // 60}h {(minutes % 60):02d}m"
    if mode == 1:
        return f"{round(minutes / 60, 1):g}h"


class CollectData:
    def __init__(self, update, on_demand = False, *args):
        self.on_demand = on_demand
        if on_demand:
            (self.job_id, self.job_type, self.start_date, self.job_message_id,
            self.job_chat_id, self.order_number, 
            self.cur_day, self.user_id, self.is_caption) = args
        else:
            message = update["message"]
            reply = message["reply_to_message"]
            user = update.effective_user
            try:
                # Job posted to channel
                self.job_chat_id = reply["forward_from_chat"]["id"]
                self.job_message_id = reply["forward_from_message_id"]
            except TypeError:
                # Job posted to chat
                self.job_chat_id = message["chat"]["id"]
                self.job_message_id = reply["message_id"]

            self.user_id = user["id"]
            self.username = user["username"]
            self.user_firstname = user["first_name"]

            self.chat_id_user_reply = message["chat"]["id"]
            self.message_id_user_reply = message["message_id"]

            self.is_caption = bool(reply.caption)

            # Getting active jobs if they exist
            try:
                (
                    self.job_id, self.start_date, self.cur_day, self.job_type, self.order_number,
                    self.sticker_id, self.sticker_power, self.yesterday_work,
                ) = db_query(
                    f"""select jobs.id, jobs.created, DATE_PART('day', now()-jobs.created), type, order_number , stickers.id, power, count(jobs_updates.created)
                        from jobs left join jobs_updates 
                        on jobs.id=jobs_updates.job_id and user_id={self.user_id} and date_part('day', jobs_updates.created - jobs.created) = least(4, DATE_PART('day', now()-jobs.created) - 1)
                        left join stickers on stickers.text_id = '{message['sticker']['file_unique_id']}'
                        where message_id = {self.job_message_id} 
                        and chat_id = {self.job_chat_id} 
                        and DATE_PART('day', now()-jobs.created) < 7
                        group by jobs.id, jobs.created, DATE_PART('day', now()-jobs.created), type, order_number , stickers.id, power
                """
                )[0]
            except IndexError:
                # Job not exists
                self.job_id = self.sticker_id = None


def rebuild_message(context, data):
    text = db_query(
        f"select caption from post_templates where job_type = {data.job_type}"
    )[0][0]
    text = fill_template(text, data.order_number, data.start_date)
    text, work_today = get_posted_message(text, data)

    try:
        if data.is_caption:
            context.bot.edit_message_caption(
                chat_id=data.job_chat_id,
                message_id=data.job_message_id,
                caption=text,
                parse_mode=ParseMode.HTML,
            )
        else:
            context.bot.edit_message_text(
                text=text,
                chat_id=data.job_chat_id,
                message_id=data.job_message_id,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        if not data.on_demand:
            logger.info(
                f"Edited job with id {data.job_id} after posted sticker id {data.sticker_id} by @{data.username} with firstname {data.user_firstname}"
            )

            if work_today == data.sticker_power:
                first_today = True
                question = Responses.get(data.job_type, 1)
                greet = Responses.get(data.job_type, 2)
                line = '' if greet == '' else '\n\n'
                text = f"Молодец! День {int(data.cur_day+1)} выполнен!\n\n{greet + line + question}"
            else:
                first_today = False
                text = f"За сегодня добавлено {minutes_to_hours(work_today)}!"

            bot_message_to_chat(
                context, data.chat_id_user_reply, text, 0 if first_today else 60, data.message_id_user_reply, ParseMode.HTML
            )

    except BadRequest:
        pass


def get_posted_message(text, data):
    EM_TRUE = "✅"
    EM_FALSE = "⚫️"
    EM_FAIL = "❌"
    WEEKEND = "🔥"
    USERS = "<b>Участники</b>"

    # Collecting data about current job progress
    query = db_query(
        f"""select user_id, first_name, total, d0, d1, d2, d3, d4, d5, d6
                from
                    (select user_id , sum(case when sday = 0 then power else 0 end) d0, sum(case when sday = 1 then power else 0 end) d1
                                        , sum(case when sday = 2 then power else 0 end) d2, sum(case when sday = 3 then power else 0 end) d3
                                        , sum(case when sday = 4 then power else 0 end) d4, sum(case when sday = 5 then power else 0 end) d5
                                        , sum(case when sday = 6 then power else 0 end) d6, sum(power) total
                    from
                        (select user_id, date_part('day', jobs_updates.created - jobs.created) as sday, sticker_id
                        from jobs_updates join jobs on jobs.id = jobs_updates.job_id
                        where job_id = {data.job_id}) t join stickers on stickers.id = t.sticker_id
                    group by user_id) t 
                    join users on users.id=t.user_id 
                    order by total desc
                ;"""
    )

    text = text.split(f"\n\n{USERS}:")[0]

    passed = list()
    loosers = list() 
    work_today = 0
    
    for user_id, user_firstname, total, *days in query:
        is_first_fail = True
        weekends = list()
        # chr(8206) is a mark to keep text format left to right
        name_phrase = (
            f'{chr(8206)}<a href="tg://user?id={user_id}">{user_firstname}</a>'
        )
        phrase = str()

        for i, work in enumerate(days):
            work = int(work)

            # Checking if today is the first activity of user
            if user_id == data.user_id and i == data.cur_day:
                work_today = work
            
            if i >= 5:
                if work > 0:
                    weekends.append(WEEKEND)
            elif work == 0 and is_first_fail and i < data.cur_day:
                phrase += EM_FAIL
                is_first_fail = False
            elif work > 0:
                phrase += EM_TRUE
            else:
                phrase += EM_FALSE

        phrase += f" {minutes_to_hours(total)}"
        weekends = ''.join(weekends)

        if is_first_fail:
            passed.append((name_phrase, phrase, weekends))
        else:
            loosers.append((name_phrase, phrase))

    added_text = str()

    for i, (name_phrase, phrase, weekends) in enumerate(passed):
        added_text += f"{i+1}. {name_phrase} {weekends}\n{phrase}\n\n"

    for j, (name_phrase, phrase) in enumerate(loosers):
        added_text += f"{i + j + 2}. <s>{name_phrase}</s>\n{phrase}\n\n"
    text += "\n\n" + added_text

    return text, work_today
