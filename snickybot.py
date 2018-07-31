#!/usr/bin/env python3

from slackclient import SlackClient
from icalevents import icalevents
from datetime import datetime, timezone, timedelta
import time
import os
import re
import argparse

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
CALENDAR_URL = 'https://calendar.google.com/calendar/ical/ncss.edu.au_7hiaq9tlca1lsgfksjss4ejc4s%40group.calendar.google.com/private-23775cab8b8397efb35dd7f2b6e67d84/basic.ics'
RE_SLACKID = re.compile('<@(\w+)>')
LOOKUP_FILE = "username_log"
REACTION_FILE = "reaction_log"
OHNO_USERS = ['UBV5SETED', 'UBZ7T5C30']  # nicky and josie
SLEEP_MINUTES = 1

CHALLENGE_TIME_OFFSET = 10  # fixed hour offset
UTCHOURS_ACTIVE_START = (9 - CHALLENGE_TIME_OFFSET) % 24
UTCHOURS_ACTIVE_END = (21 - CHALLENGE_TIME_OFFSET) % 24

# nb. test value on left, real value on right
MINUTES_NOUSERS = args.test and 40 or 20  # max is 60, won't be checked before current hour
MINUTES_NOTIFY = args.test and 120 or 10
MINUTES_DANGER = args.test and 5 or 1
CHANNEL = args.test and "CBXDYDGFP" or "CBVLC2MU3"

assert(MINUTES_NOUSERS > MINUTES_NOTIFY)
assert(MINUTES_NOTIFY > MINUTES_DANGER)

if args.test:
  print("snickybot in TEST MODE")
else:
  print("snickybot in PROD MODE")
print("notify {}min, danger {}min".format(MINUTES_NOTIFY, MINUTES_DANGER))
print()

tutors_dict = {}  # real name to slackid

# read whole file and fill it in
try:
  with open(LOOKUP_FILE) as f:
    for line in f:
      line = line.strip()
      parts = line.split(',', 1)
      if len(parts) != 2:
        if line:
          print("bad line: {}".format(line))
        continue
      foundid, sourcename = parts
      tutors_dict[sourcename] = foundid
except IOError:
  pass # probably doesn't exist

for sourcename in tutors_dict:
  print("added user from db: {} => {}".format(sourcename, tutors_dict[sourcename]))

# now open it again to append more logs
username_file = open(LOOKUP_FILE, 'a')
reaction_file = open(REACTION_FILE, 'a')

# connect to Slack
sc = SlackClient(SLACK_TOKEN)

# connect to RTM API which feeds us stuff that happens
if not sc.rtm_connect(with_team_state=False, auto_reconnect=True):
  raise Exception("couldn't connect to RTM api")
sc.rtm_send_message("welcome-test", "test")


def is_checked_hour(hour):
  if UTCHOURS_ACTIVE_START > UTCHOURS_ACTIVE_END:
    # start later than end
    return hour >= UTCHOURS_ACTIVE_START or hour < UTCHOURS_ACTIVE_END
  # normal contiguous range
  return hour >= UTCHOURS_ACTIVE_START and hour < UTCHOURS_ACTIVE_END


def format_real_name(real_name):
  if real_name in tutors_dict:
    slackid = tutors_dict[real_name]
    return '<@{}>'.format(slackid)
  return '{}'.format(real_name)


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


def get_pending_tutor_cals(now):
  out = []
  evs = icalevents.events(url=CALENDAR_URL)
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


def get_members(members, tutors_dict):
  # Woo Thought this was paginated but:
  # At this time, providing no limit value will result in Slack attempting to deliver you the entire result set. If the collection is too large you may experience HTTP 500 errors. Resolve this scenario by using pagination.
  if 'members' not in members:
    print('Got no members, maybe rate-limited: {}'.format(members))
    return
  for member in members['members']:
    slackid = member['id']
    real_name = member['real_name']
    if real_name not in tutors_dict:
      tutors_dict[real_name] = slackid


def extract_name_from_cal(next_tutor_cal):
  #next_tutor_cal.summary is something like:  #NCSS Tutoring (Firstname Lastname)
  summary = next_tutor_cal.summary
  summary = summary.replace(chr(65288), '(')  # ??? some people have a weird start bracket
  name = summary.replace('NCSS Tutoring (','')[:-1]
  print('Name from calendar: {} => {}'.format(summary, name))
  return(name)


def sendmsg(text, threadid=None, attach=None):
  if args.silent:
    print('Silent mode, not sending message (threadid={}): {}'.format(threadid, text))
    return {'ts': 'TODO'}

  kwargs = {
    'channel': CHANNEL,
    'text': text,
  }
  if threadid:
    kwargs['thread_ts'] = threadid
  if attach:
    kwargs['attachments'] = attach
  message = sc.api_call("chat.postMessage", **kwargs)
  if threadid:
    print('Replied to thead {}: {}'.format(threadid, text))
  else:
    print('Messaged channel: {}'.format(text))
  return message


def message_tutor(slackid, name, impending_tutor_time):
  time_format = pretty_time_delta(impending_tutor_time)
  if slackid:
    text = ":smile: <@{}>'s ({}) shift starts in {}. Please ack with an emoji reaction.".format(slackid, name, time_format)
  else:
    text = ":smile: {}'s shift starts in {}, but I don't know their Slack username. Please reply to this thread with an @mention of their username to let me know who they are!".format(name, time_format)
  return sendmsg(text)


msg_id_to_watch = {}    # messages posted about calendar events (contains {sourcename, calid})
already_announced = {}  # calendar events processed and posted about


def handle_event(event):
  if event['type'] == 'reaction_added':
    return handle_event_reaction_added(event)
  elif event['type'] == 'message':
    return handle_event_message(event)


def handle_event_reaction_added(event):
  msgid = event['item']['ts']
  userid = event['user']

  prev_msg = msg_id_to_watch.get(msgid, None)
  if not prev_msg:
    return  # some other message

  slackid = tutors_dict.get(prev_msg['sourcename'], '')
  if slackid != userid:
    # if we don't know their slackid then they can't ack this :(
    return  # not the user we care about

  print('Correct person acked, so deleting msgid {} from msg_id_to_watch'.format(msgid))
  del msg_id_to_watch[msgid]
  calid = prev_msg['calid']
  already_announced[calid]['acked'] = True

  sendmsg("Thanks <@{}>! :+1::star-struck:".format(userid), threadid=msgid)
  print("user {} acked tutoring with {}".format(userid, event['reaction']))
  reaction_file.write('{},{}\n'.format(userid, event['reaction']))
  reaction_file.flush()


def handle_event_message(event):
  if 'thread_ts' not in event:
    return  # not a thread reply

  threadid = event['thread_ts']
  if threadid not in msg_id_to_watch:
    return  # not a thread we care about

  data = msg_id_to_watch[threadid]
  print("got reply to interesting thread: {} ({})".format(event['text'], msg_id_to_watch[threadid]))

  out = RE_SLACKID.match(event['text'])
  if not out:
    return  # no userid
  foundid = out.group(1)
  tutors_dict[data['sourcename']] = foundid
  print("connected '{}' to Slack: {}".format(data['sourcename'], foundid))
  username_file.write("{},{}\n".format(foundid, data['sourcename']))
  username_file.flush()

  # if reply contains syntax: <@UBWNYRKDX> map to user
  sendmsg("Thanks! I've updated {}'s slack ID to be <@{}> -- please ack the original message with an emoji reaction. :+1:".format(data['sourcename'], foundid), threadid=threadid)


checked_hour = None  # the hour checked up to
ohno_users_text = ', '.join(['<@{}>'.format(user) for user in OHNO_USERS])

while True:
  members = sc.api_call("users.list")
  get_members(members, tutors_dict)

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
    local_hour = checked_hour + CHALLENGE_TIME_OFFSET
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
    sendmsg("<!here> Warning! There's no tutors rostered on at {}:00! ({})".format(local_hour, ohno_users_text), attach=attach)

  for calid in list(already_announced.keys()):  # we might modify this during iteration
    # TODO: Expire sessions a long time after they start??
    data = already_announced[calid]
    unacked_cal = data['cal']
    msgid = data['msgid']
    if data['acked']:
      # this has already been ack'd by an emoji.
      print('Checking upcoming cals. Skipping {} as it was already acked'.format(msgid))
      continue
    if msgid not in msg_id_to_watch:
      # Time's up, bot alerted Nicky/Josie, we removed msg.
      continue
    prev_msg = msg_id_to_watch[msgid]

    minutes_away = (unacked_cal.start - now).total_seconds() / 60  # negative if we've gone past no
    print('minutes_away ({}): {}'.format(prev_msg['sourcename'], minutes_away))
    if minutes_away > MINUTES_DANGER:
      continue

    who_text = format_real_name(prev_msg['sourcename'])
    ohno_text = ', '.join(['<@{}>'.format(user) for user in OHNO_USERS])
    sendmsg("Oh no! {} hasn't responded. Pinging {}".format(who_text, ohno_users_text), threadid=msgid)
    del msg_id_to_watch[msgid]

  # sleep for 60s but check if we have events
  for i in range(0, max(1, SLEEP_MINUTES * 60)):
    events = sc.rtm_read()
    for event in events:
      handle_event(event)
    time.sleep(1)
  print(".")



