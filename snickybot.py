from slackclient import SlackClient
from icalevents import icalevents
from datetime import datetime, timezone, timedelta
import time
import os
import re

SLACK_TOKEN = os.environ.get('SLACK_API_TOKEN', '')
if not SLACK_TOKEN:
  raise Exception('please set SLACK_API_TOKEN in env')

CALENDAR_URL = 'https://calendar.google.com/calendar/ical/ncss.edu.au_7hiaq9tlca1lsgfksjss4ejc4s%40group.calendar.google.com/private-23775cab8b8397efb35dd7f2b6e67d84/basic.ics'
RE_SLACKID = re.compile('<@(\w+)>')
LOOKUP_FILE = "username_log"
MINUTES_NOTIFY = 10  # should be 5?
MINUTES_DANGER = 1
CHANNEL = "CBXDYDGFP" # TEST SLACK
CHANNEL = "CBVLC2MU3"
OHNO_USERS = ['UBV5SETED', 'UBZ7T5C30']  # nicky and josie

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
      tutors_dict[sourcename] = {'id': foundid, 'real_name': sourcename}
except IOError:
  pass # probably doesn't exist

for sourcename in tutors_dict:
  print("got known usermap: {} => {}".format(sourcename, tutors_dict[sourcename]))

# now open it again to append more logs
username_file = open(LOOKUP_FILE, 'a')

# connect to Slack
sc = SlackClient(SLACK_TOKEN)

# connect to RTM API which feeds us stuff that happens
if not sc.rtm_connect(with_team_state=False, auto_reconnect=True):
  raise Exception("couldn't connect to RTM api")
sc.rtm_send_message("welcome-test", "test")


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


def get_pending_tutor_cals(now, within=MINUTES_NOTIFY):
  out = []
  evs = icalevents.events(url=CALENDAR_URL)
  #'all_day', 'copy_to', 'description', 'end', 'start', 'summary', 'time_left', 'uid'
  evs.sort(key=lambda ev: now - ev.start, reverse=True)

  for ev in evs:
    in_minutes = (now - ev.start).total_seconds() / 60.0
    if in_minutes * -1 < within and in_minutes < 0:
      out.append(ev)
  return out


def event_is_same(ev1, ev2):
  if not ev1 or not ev2:
    return ev1 == ev2
  return ev1.uid == ev2.uid


def get_members(members, tutors_dict):
  #Woo Thought this was paginated but:
  #At this time, providing no limit value will result in Slack attempting to deliver you the entire result set. If the collection is too large you may experience HTTP 500 errors. Resolve this scenario by using pagination.
  for member in members['members']:
    id = member['id']
    real_name = member['real_name']
    name = member['real_name']
    if name not in tutors_dict:
      tutors_dict[name] = {
              'id':id,
              'name':name,
              'real_name':real_name
              }


def extract_name_from_cal(cal_summary):
  #next_tutor_cal.summary is something like:  #NCSS Tutoring (Firstname Lastname)
  name = next_tutor_cal.summary.replace('NCSS Tutoring (','')[:-1]
  print('Name from calendar: {}'.format(name))
  return(name)


def match_tutor(next_tutor_cal, tutor_list):
  name = extract_name_from_cal(next_tutor_cal)
  if name in tutor_list:
    print('Huzzah! Found a match: {} == {}'.format(name, tutor_list[name]))
    #message_tutor(tutor_list[name])
    return tutor_list[name]
  else:
    print('No match found.')
    return None


def sendmsg(text, threadid=None):
  kwargs = {
    'channel': CHANNEL,
    'text': text,
  }
  if threadid:
    kwargs['thread_ts'] = threadid
  message = sc.api_call("chat.postMessage", **kwargs)
  return message


def message_tutor(slack_tutor, impending_tutor_time):
  impending_tutor_time = pretty_time_delta(impending_tutor_time)
  return sendmsg(":smile: <@{}>'s ({}) shift starts in {}. Please ack with an emoji reaction.".format(slack_tutor['id'], slack_tutor['real_name'], (impending_tutor_time)))


def message_unknown_tutor(name, impending_tutor_time):
  impending_tutor_time = pretty_time_delta(impending_tutor_time)
  return sendmsg(":smile: {}'s shift starts in {}, but I don't know their slack ID. Please reply to this thread with an @mention of their username to let me know who they are!".format(name, (impending_tutor_time)))


name_to_slackid = {}
msg_id_to_watch = {}
already_announced = {}


def handle_event(event):
  print('got event: {}'.format(event))

  if event['type'] == 'reaction_added':
    msgid = event['item']['ts']
    userid = event['user']

    if msgid not in msg_id_to_watch:
      return  # some other message
    prev_msg = msg_id_to_watch[msgid]
    if prev_msg['slackid'] != userid:
      return  # not the user we care about
    print('Correct person acked, so deleting msgid {} from msg_id_to_watch:\n{}'.format(msgid, msg_id_to_watch))
    del msg_id_to_watch[msgid]
    x = prev_msg['calid']
    already_announced[x]['acked'] = True

    sendmsg("Thanks <@{}>! :+1::star-struck:".format(userid), threadid=msgid)
    print("user {} acked tutoring with {}", userid, event['reaction'])
    return

  if event['type'] != "message":
    return  # ignore for now

  if 'thread_ts' not in event:
    return  # not a thread reply

  print('msg_id_to_watch: {}'.format(msg_id_to_watch))
  print('already_announced: {}'.format(already_announced))
  print()
  threadid = event['thread_ts']
  if threadid not in msg_id_to_watch:
    return  # not a thread we care about

  data = msg_id_to_watch[threadid]
  print("got reply to interesting thread: {} ({})".format(event['text'], msg_id_to_watch[threadid]))

  out = RE_SLACKID.match(event['text'])
  if not out:
    return  # no userid
  foundid = out.group(1)
  tutors_dict[data['sourcename']] = {'id': foundid, 'real_name': data['sourcename']}
  print("connected '{}' to Slack: {}".format(data['sourcename'], foundid))
  username_file.write("{},{}\n".format(foundid, data['sourcename']))
  username_file.flush()

  # if reply contains syntax: <@UBWNYRKDX> map to user
  sendmsg("Thanks! I've updated {}'s slack ID to be <@{}> -- please ack the original message with an emoji reaction. :+1:".format(data['sourcename'], foundid), threadid=threadid)

while True:
  members = sc.api_call("users.list")
  get_members(members, tutors_dict)

  now = datetime.now(timezone.utc)
  #try:
  pending = get_pending_tutor_cals(now)
  #except TimeoutError:
  #  print('TimeoutError. Skipping for now.')
  for next_tutor_cal in pending:
    # SO it turns out that Google thinks -1 is a great uid for all events. 
    calid = '{}-{}'.format(next_tutor_cal.start, next_tutor_cal.summary)
    if calid in already_announced:
      continue  # don't announce a second time

    # get tutor and their name if possible
    slack_tutor = match_tutor(next_tutor_cal, tutors_dict)
    name = extract_name_from_cal(next_tutor_cal)

    impending_tutor_time = -(now - next_tutor_cal.start)
    if slack_tutor != None:
      # we know who they are on slack
      m = message_tutor(slack_tutor, impending_tutor_time)
      slackid = slack_tutor['id']
    else:
      # we don't know who they are
      m = message_unknown_tutor(name, impending_tutor_time)
      slackid = None

    # save for later
    print(m)
    msg_id_to_watch[m['ts']] = {'sourcename': name, 'slackid': slackid, 'calid': calid}
    already_announced[calid] = {
      'cal': next_tutor_cal,
      'msgid': m['ts'],
      'acked': False,
    }

  for calid in list(already_announced.keys()):  # we might modify this during iteration
    data = already_announced[calid]
    unacked_cal = data['cal']
    msgid = data['msgid']
    already_acked = data['acked']
    if already_acked:
      # this has already been ack'd by an emoji.
      print('Checking upcoming cals. Skipping {} as it was already acked'.format(msgid))
      continue
    if msgid not in msg_id_to_watch:
      # Time's up, bot alerted Nicky/Josie, we removed msg.
      continue
    prev_msg = msg_id_to_watch[msgid]

    minutes_away = (unacked_cal.start - now).total_seconds() / 60  # negative if we've gone past no
    print('minutes_away: {}'.format(minutes_away))
    if minutes_away > MINUTES_DANGER:
      continue

    if prev_msg['slackid']:
      who = "<@{}>".format(prev_msg['slackid'])
    else:
      who = "{}".format(prev_msg['sourcename'])

    ohno_text = ', '.join(['<@{}>'.format(user) for user in OHNO_USERS])
    sendmsg("Oh no! {} hasn't responded. Pinging {}".format(who, ohno_text), threadid=msgid)
    del msg_id_to_watch[msgid]
    #del already_announced[calid] #don't delete it from already_announced

  # sleep for 60s but check if we have events
  for i in range(0, 60):
    events = sc.rtm_read()
    for event in events:
      handle_event(event)
    time.sleep(1)
  print(".", end="")



