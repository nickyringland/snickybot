from slackclient import SlackClient
from icalevents import icalevents
from datetime import datetime, timezone, timedelta
import time
import os
import re

RE_SLACKID = re.compile('<@(\w+)>')
LOOKUP_FILE = "username_log"

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
  print("got known usermap: {} => {}", sourcename, tutors_dict[sourcename])

# now open it again to append more logs
username_file = open(LOOKUP_FILE, 'a')

slack_token = os.environ["SLACK_API_TOKEN"]
print("Got token: {}".format(slack_token))
sc = SlackClient(slack_token)
url = 'https://calendar.google.com/calendar/ical/ncss.edu.au_7hiaq9tlca1lsgfksjss4ejc4s%40group.calendar.google.com/private-23775cab8b8397efb35dd7f2b6e67d84/basic.ics'
channel="CBXDYDGFP"

# connect to RTM API which feeds us stuff that happens
if not sc.rtm_connect(with_team_state=False, auto_reconnect=True):
  raise Error("couldn't connect to RTM api")
sc.rtm_send_message("welcome-test", "test")

def get_next_tutor_cal(now):
  evs = icalevents.events(url=url)
  #'all_day', 'copy_to', 'description', 'end', 'start', 'summary', 'time_left', 'uid'
  evs.sort(key=lambda ev: now - ev.start, reverse=True)

  for ev in evs:
    if (now - ev.start).total_seconds() < 0:
      #this is the first one in the future
      return(ev)


def event_is_same(ev1, ev2):
  if not ev1 or not ev2:
    return ev1 == ev2
  return ev1.uid == ev2.uid


def get_slack_members():
  slack_token = os.environ["SLACK_API_TOKEN"]
  sc = SlackClient(slack_token)

  a = sc.api_call("users.list")
  return(a)


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
    #print('getting ' + real_name)


def message_tutor(tutor):
  print('messaging {}'.format(tutor['real_name']))
  message = sc.api_call(
    'chat.postMessage',
    channel=channel,
    text='<@{}> This is a test.'.format(tutor['id'])
    )


def extract_name_from_cal(cal_summary):
  #next_tutor_cal.summary is something like:
  #NCSS Tutoring (Nicky Ringland)
  #wooo lazy
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


def message_tutor(slack_tutor, impending_tutor_time):
  message = sc.api_call(
    "chat.postMessage",
    channel=channel,
    text=":smile: <@{}>'s ({}) shift starts in {}. Please ack with an emoji reaction.".format(slack_tutor['id'], slack_tutor['real_name'], (impending_tutor_time))
  )
  return message

def message_unknown_tutor(name, impending_tutor_time):
  message = sc.api_call(
    "chat.postMessage",
    channel=channel,
    text=":smile: {}'s shift starts in {}, but I don't know their slack ID. Please reply to this thread with an @mention of their username to let me know who they are!".format(name, (impending_tutor_time))
  )
  return message

name_to_slackid = {}
msg_id_to_watch = {}
announced_next_tutor_cal = None

def handle_event(event):
  # TODO: look for emoji reactions

  if event['type'] != "message":
    return  # ignore for now

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
  tutors_dict[data['sourcename']] = {'id': foundid, 'real_name': data['sourcename']}
  print("connected '{}' to Slack: {}".format(data['sourcename'], foundid))
  username_file.write("{},{}\n".format(foundid, data['sourcename']))
  username_file.flush()

  # if reply contains syntax: <@UBWNYRKDX> map to user
  # TODO: reply to thread, don't just post a new message
  message = sc.api_call(
    "chat.postMessage",
    channel=channel,
    text="Thanks! I've updated {}'s slack ID to be <@{}> -- please ack this message with an emoji reaction. :+1:".format(data['sourcename'], foundid)
  )


while True:
  members =get_slack_members()
  get_members(members, tutors_dict)

  now = datetime.now(timezone.utc)
  next_tutor_cal = get_next_tutor_cal(now)
  impending_tutor_time = -(now - next_tutor_cal.start)

  if not event_is_same(next_tutor_cal, announced_next_tutor_cal):
    slack_tutor = match_tutor(next_tutor_cal, tutors_dict)
    name = extract_name_from_cal(next_tutor_cal)
    if slack_tutor != None:
      m = message_tutor(slack_tutor, impending_tutor_time)
      msg_id_to_watch[m['ts']] = {'sourcename': name, 'slackid': slack_tutor['id']}
    else:
      m = message_unknown_tutor(name, impending_tutor_time)
      msg_id_to_watch[m['ts']] = {'sourcename': name, 'slackid': None}
    announced_next_tutor_cal = next_tutor_cal
  print('checking ical next tutor {}.'.format(announced_next_tutor_cal.summary))
  if impending_tutor_time.total_seconds() < (5*60):
    sc.api_call(
      "chat.postMessage",
      channel=channel,
      text="remind @{} - {} starting soon! plz ack".format(tutors['Nicky Ringland'],next_tutor_cal.summary)
    )

  # sleep for 60s but check if we have events
  for i in range(0, 60):
    events = sc.rtm_read()
    for event in events:
      handle_event(event)
    time.sleep(1)




