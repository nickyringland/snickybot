#!/usr/bin/env python3

import slack
import asyncio
import redis
from cachetools.func import ttl_cache
from icalevents import icalevents
from datetime import datetime, timezone, timedelta
import time
import os
import re
import argparse
import random  # for testing

parser = argparse.ArgumentParser()
parser.add_argument('--test', '-t', action='store_true',
                    help='whether to run in test mode')
parser.add_argument('--token', '-x', type=str,
                    default=os.environ.get('SLACK_API_TOKEN', ''),
                    required=('SLACK_API_TOKEN' not in os.environ),
                    help='the API token to use')
parser.add_argument('--silent', '-s', action='store_true',
                    help='whether to skip sending messages (for testing)')
args = parser.parse_args()

SLACK_TOKEN = args.token
CALENDAR_URL = os.environ['CALENDAR_URL']
OHNO_USERS = os.environ['OHNO_USERS'].split(',')
CHANNEL = os.environ['CHANNEL']
REDIS_ADDRESS = os.environ['REDIS_ADDRESS']
REDIS_DB = int(os.environ['REDIS_DB'])

RE_SLACKID = re.compile('<@(\w+)>')
AMENDED_REALNAMETOSLACK_KEY = 'snickybot:amended_realnametoslack'
SLEEP_MINUTES = 1
CHALLENGE_TIME_OFFSET = 10  # fixed hour offset
UTCHOURS_ACTIVE_START = (9 - CHALLENGE_TIME_OFFSET) % 24
UTCHOURS_ACTIVE_END = (21 - CHALLENGE_TIME_OFFSET) % 24

# nb. test value on left, real value on right
MINUTES_NOUSERS = args.test and 40 or 20  # max is 60, won't be checked before current hour
MINUTES_NOTIFY = args.test and 120 or 10
MINUTES_DANGER = args.test and 5 or 1

if args.test:
  print("snickybot in TEST MODE")
else:
  print("snickybot in PROD MODE")
print("nouser warning {}min, notify {}min, danger {}min".format(MINUTES_NOUSERS, MINUTES_NOTIFY, MINUTES_DANGER))
print()

# connect to things
sc = slack.WebClient(SLACK_TOKEN, run_async=True)
r = redis.Redis(host=REDIS_ADDRESS, db=REDIS_DB)


def is_checked_hour(hour):
  if UTCHOURS_ACTIVE_START > UTCHOURS_ACTIVE_END:
    # start later than end
    return hour >= UTCHOURS_ACTIVE_START or hour < UTCHOURS_ACTIVE_END
  # normal contiguous range
  return hour >= UTCHOURS_ACTIVE_START and hour < UTCHOURS_ACTIVE_END


def pretty_time_delta(td):
  seconds = int(td.total_seconds())
  seconds = abs(seconds)
  days, seconds = divmod(seconds, 86400)
  hours, seconds = divmod(seconds, 3600)
  minutes, seconds = divmod(seconds, 60)
  if days > 0:
    return '%dd%dh%dm%ds' % (days, hours, minutes, seconds)
  elif hours > 0:
    return '%dh%dm%ds' % (hours, minutes, seconds)
  elif minutes > 0:
    return '%dm%ds' % (minutes, seconds)
  else:
    return '%ds' % (seconds)


@ttl_cache(ttl=60*5)
def get_events():
  return icalevents.events(url=CALENDAR_URL)


def get_pending_tutor_cals(now):
  out = []
  evs = get_events()
  #'all_day', 'copy_to', 'description', 'end', 'start', 'summary', 'time_left', 'uid'
  evs.sort(key=lambda ev: now - ev.start, reverse=True)

  for ev in evs:
    # is this in the future
    if (ev.start - now).total_seconds() > 0:
      out.append(ev)
  return out


def event_is_same(ev1, ev2):
  if not ev1 or not ev2:
    return ev1 == ev2
  return ev1.uid == ev2.uid


def extract_name_from_cal(next_tutor_cal):
  #next_tutor_cal.summary is something like:  #NCSS Tutoring (Firstname Lastname)
  summary = next_tutor_cal.summary
  summary = summary.replace(chr(65288), '(')  # ??? some people have a weird start bracket
  name = summary.replace('NCSS Tutoring (','')[:-1]
  print('Name from calendar: {} => {}'.format(summary, name))
  return(name)


async def sendmsg(text, threadid=None, attach=None):
  if args.silent:
    print('Silent mode, not sending message (threadid={}): {}'.format(threadid, text))
    return {'ts': 'TODO-{}'.format(random.random())}

  kwargs = {
    'channel': CHANNEL,
    'text': text,
  }
  if threadid:
    kwargs['thread_ts'] = threadid
  if attach:
    kwargs['attachments'] = attach
  response = await sc.chat_postMessage(**kwargs)
  assert response['ok']
  if threadid:
    print('Replied to thead {}: {}'.format(threadid, text))
  else:
    print('Messaged channel: {}'.format(text))
  return response['message']


async def message_tutor(slackid, name, impending_tutor_time):
  time_format = pretty_time_delta(impending_tutor_time)
  if slackid:
    text = ":smile: <@{}>'s ({}) shift starts in {}. Please ack with an emoji reaction.".format(slackid, name, time_format)
  else:
    text = ":smile: {}'s shift starts in {}, but I don't know their Slack username. Please reply to this thread with an @mention of their username to let me know who they are!".format(name, time_format)
  return sendmsg(text)


tutors_dict = {}        # real name to slackid
msg_id_to_watch = {}    # messages posted about calendar events (contains {sourcename, calid})
already_announced = {}  # calendar events processed and posted about


def format_real_name(real_name):
  if real_name in tutors_dict:
    slackid = tutors_dict[real_name]
    return '<@{}>'.format(slackid)
  return '{}'.format(real_name)


def add_tutor(member):
  slackid = member['id']
  real_name = member['real_name']
  if real_name not in tutors_dict:
    print('got member: {} => {}'.format(real_name, slackid))
    tutors_dict[real_name] = slackid


async def load_tutors_dict():
  # Woo Thought this was paginated but:
  # At this time, providing no limit value will result in Slack attempting to deliver you the entire result set. If the collection is too large you may experience HTTP 500 errors. Resolve this scenario by using pagination.
  response = await sc.users_list()
  assert response['ok']

  for member in response['members']:
    add_tutor(member)

  for (real_name, slackid) in r.hgetall(AMENDED_REALNAMETOSLACK_KEY).items():
    real_name = real_name.decode('utf-8')
    slackid = slackid.decode('utf-8')
    print('loading amended member: {} => {}'.format(real_name, slackid))


@slack.RTMClient.run_on(event='member_joined_channel')
async def rtm_member_joined_channel(data, **kwargs):
  response = await sc.users_info(user=data['user'])
  assert response['ok']
  add_tutor(response['user'])


@slack.RTMClient.run_on(event='user_change')
async def rtm_user_change(data, **kwargs):
  add_tutor(data['user'])


@slack.RTMClient.run_on(event='reaction_added')
async def rtm_reaction_added(data, **kwargs):
  event = data
  msgid = event['item']['ts']
  userid = event['user']

  prev_msg = msg_id_to_watch.get(msgid, None)
  if not prev_msg:
    return  # some other message

  slackid = tutors_dict.get(prev_msg['sourcename'], '')
  if slackid != userid:
    # if we don't know their slackid then they can't ack this :(
    print("[{}] got reaction from non-target user: {}".format(msgid, event['reaction']))
    return  # not the user we care about

  del msg_id_to_watch[msgid]
  calid = prev_msg['calid']
  already_announced[calid]['acked'] = True

  await sendmsg("Thanks <@{}>! :+1::star-struck:".format(userid), threadid=msgid)
  print("[{}] slack user {} acked tutoring with: {}".format(msgid, userid, event['reaction']))


@slack.RTMClient.run_on(event='message')
async def rtm_message(data, **kwargs):
  event = data
  if 'thread_ts' not in event:
    return  # not a thread reply

  threadid = event['thread_ts']
  if threadid not in msg_id_to_watch:
    return  # not a thread we care about

  data = msg_id_to_watch[threadid]

  out = RE_SLACKID.match(event['text'])
  if not out:
    print("[{}] got reply to watched thread, ignoring: {}".format(threadid, event['text']))
    return  # no userid
  foundid = out.group(1)
  tutors_dict[data['sourcename']] = foundid
  r.hset(AMENDED_REALNAMETOSLACK_KEY, data['sourcename'].encode('utf-8'), foundid.encode('utf-8'))
  print("[{}] connected '{}' to Slack: {}".format(threadid, data['sourcename'], foundid))

  # if reply contains syntax: <@UBWNYRKDX> map to user
  await sendmsg("Thanks! I've updated {}'s Slack username to be <@{}> -- please ack the original message with an emoji reaction. :+1:".format(data['sourcename'], foundid), threadid=threadid)


checked_hour = None  # the hour checked up to
ohno_users_text = ', '.join(['<@{}>'.format(user) for user in OHNO_USERS])

async def process_calendar():
  global checked_hour
  now = datetime.now(timezone.utc)  # calendar data is in UTC

  # do we need to notify that we're missing tutors?
  notify_missing_tutors = False
  next_check_hour = (now + timedelta(hours=1)).hour
  if 60 - now.minute < MINUTES_NOUSERS:
    if checked_hour != next_check_hour and is_checked_hour(next_check_hour):
      # if we find a valid hour below, set this to False
      notify_missing_tutors = True
    checked_hour = next_check_hour

  #try:
  pending = get_pending_tutor_cals(now)
  #except TimeoutError:
  #  print('TimeoutError. Skipping for now.')
  print("got {} pending cal events at {}".format(len(pending), now))
  for next_tutor_cal in pending:
    if next_tutor_cal.start.hour == next_check_hour:
      # got an event starting in the next hour
      if notify_missing_tutors:
        print("got event starting at {}:00, don't need to notify: {}".format(next_check_hour + CHALLENGE_TIME_OFFSET, next_tutor_cal))
      notify_missing_tutors = False

    # don't notify them, not close enoughb
    in_minutes = (next_tutor_cal.start - now).total_seconds() / 60.0
    if in_minutes >= MINUTES_NOTIFY:
      break

    # SO it turns out that Google thinks -1 is a great uid for all events.
    calid = '{}-{}'.format(next_tutor_cal.start, next_tutor_cal.summary)
    if calid in already_announced:
      continue  # don't announce a second time

    # they start after this time
    impending_tutor_time = -(now - next_tutor_cal.start)

    # get tutor name and their slackid if possible
    name = extract_name_from_cal(next_tutor_cal)
    slackid = tutors_dict.get(name, None)

    # send them a message (slackid might be None) and save it for later
    m = message_tutor(slackid, name, impending_tutor_time)
    msg_id_to_watch[m['ts']] = {'sourcename': name, 'calid': calid}
    already_announced[calid] = {
      'cal': next_tutor_cal,
      'msgid': m['ts'],
      'acked': False,
    }

  if notify_missing_tutors:
    # timezones are hard.
    local_hour = (checked_hour + CHALLENGE_TIME_OFFSET) % 24
    attach = [
      {
        "fallback": "Add your name in the roster here: https://python.gl/tutor-roster",
        "actions": [
          {
            "type": "button",
            "text": "Update Roster",
            "url": "https://python.gl/tutor-roster",
          },
        ],
      },
    ]
    await sendmsg("<!here> Warning! There're no tutors rostered on at {}:00! ({})".format(local_hour, ohno_users_text), attach=attach)

  for calid in list(already_announced.keys()):  # we might modify this during iteration
    data = already_announced[calid]
    cal = data['cal']
    msgid = data['msgid']
    if cal.end < now:
      print('[{}] expiring calendar entry, past end time', msgid)
      del already_announced[calid]
      del msg_id_to_watch[msgid]
      continue

    if data['acked']:
      continue  # this has already been ack'd by an emoji.

    prev_msg = msg_id_to_watch.get(msgid, None)
    if not prev_msg:
      continue  # Time's up, bot alerted Nicky/Josie, we removed msg.

    event_starts = (cal.start - now)
    minutes_away = event_starts.total_seconds() / 60  # negative if we've gone past no
    print('[{}] {} shift in: {}'.format(msgid, prev_msg['sourcename'], pretty_time_delta(event_starts)))
    if minutes_away > MINUTES_DANGER:
      continue

    who_text = format_real_name(prev_msg['sourcename'])
    ohno_text = ', '.join(['<@{}>'.format(user) for user in OHNO_USERS])
    await sendmsg("Oh no! {} hasn't responded. Pinging {}".format(who_text, ohno_users_text), threadid=msgid)
    del msg_id_to_watch[msgid]


async def process_calendar_loop():
  while True:
    await process_calendar()
    print(".")
    await asyncio.sleep(SLEEP_MINUTES * 60)


async def main():
  await load_tutors_dict()
  await asyncio.gather(slack.RTMClient(token=SLACK_TOKEN, run_async=True).start(), process_calendar_loop())


asyncio.run(main())
