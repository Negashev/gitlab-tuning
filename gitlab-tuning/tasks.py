import os
import re

import ldap
import dramatiq
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.brokers.redis import RedisBroker
import requests
import gitlab
import time


from tools import resize_image
REDIS_URL = os.getenv("REDIS_URL", None)
if REDIS_URL in [None, ""]:
    broker = RabbitmqBroker(url=os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672"))
else:
    broker = RedisBroker(url=REDIS_URL)
dramatiq.set_broker(broker)

STATISTIC_URL = os.getenv("STATISTIC_URL", None)
STATISTIC_URL_TIMEOUT = int(os.getenv('STATISTIC_URL_TIMEOUT', '10'))
LDAP_URL = os.getenv('LDAP_URL', 'ldap://company.com_by:12345')
LDAP_USER = os.getenv('LDAP_USER', 'Reader@company.com_by')
LDAP_PASS = os.getenv('LDAP_PASS', 'PASS')
LDAP_BASE = os.getenv('LDAP_BASE', 'DC=company,DC=com_by')
GITLAB_URL = os.getenv('GITLAB_URL', 'https://gitlab.company.com_by')
gl = gitlab.Gitlab(GITLAB_URL, private_token=os.getenv('GITLAB_TOKEN', 'GITLAB_TOKEN'))

tmp_LDAP_GROUP_PREFIX = os.getenv('LDAP_GROUP_PREFIX', 'Group')
if tmp_LDAP_GROUP_PREFIX:
    LDAP_GROUP_PREFIX = tmp_LDAP_GROUP_PREFIX + ' '
else:
    LDAP_GROUP_PREFIX = ''
LDAP_OBJECTCLASS_GROUP = os.getenv('LDAP_OBJECTCLASS_GROUP', 'group')
LDAP_OBJECTCLASS_USER = os.getenv('LDAP_OBJECTCLASS_USER', 'user')
CRON_AVATARS_PER_PAGE = int(os.getenv('CRON_AVATARS_PER_PAGE', '10'))
CRON_PROJECTS_PER_PAGE = int(os.getenv('CRON_PROJECTS_PER_PAGE', '10'))
ACCESS_PROJECT_STARTSWITH = os.getenv('ACCESS_PROJECT_STARTSWITH', 'group')
ACCESS_PROJECT_DELIMITER = os.getenv('ACCESS_PROJECT_DELIMITER', None)
if ACCESS_PROJECT_DELIMITER is None:
    ACCESS_PROJECT_DELIMITER = ' '
RESTRICT_KEYWORD = 'RESTRICTED'
RESTRICT_PROJECT_DELIMITER='='

@dramatiq.actor(priority=10, max_retries=2)
def statistic_prepare_data(project_id, git_ssh_url, changes):
    if STATISTIC_URL in [None, ""]:
        print('STATISTIC_URL is not set')
        return
    try:
        project = gl.projects.get(project_id)
    except gitlab.exceptions.GitlabGetError as e:
        if e.error_message == '404 Project Not Found':
            return
        else:
            raise gitlab.exceptions.GitlabGetError
    for change in changes:
        time.sleep(1)
        commits = project.commits.list(query_parameters={'ref_name': change['after']}, per_page=50)
        change['after'] = commits[-1].id
        exit_recursion = False
        for commit in commits[:-1]:
            if change['before'] == commit.id:
                # exit from this ref
                exit_recursion = True
                break
            statistic_push_data.send({
                "id": commit.id,
                "author_email": commit.author_email.lower(),
                "repository": git_ssh_url,
                "created_at": commit.created_at
            })
        # recursion
        if len(commits) > 1 and not exit_recursion:
            time.sleep(1)
            statistic_prepare_data.send(project_id, git_ssh_url, [change])


@dramatiq.actor(priority=20, max_retries=3)
def statistic_push_data(data):
    requests.post(STATISTIC_URL, data=data, timeout=STATISTIC_URL_TIMEOUT)
    print(f"commit {data['id']} by {data['author_email']} in {data['repository']}")


def get_ldap_owner_with_users(group_name):
    connect = ldap.initialize(LDAP_URL)
    connect.set_option(ldap.OPT_REFERRALS, 0)
    connect.simple_bind_s(LDAP_USER, LDAP_PASS)
    r = connect.search_s(LDAP_BASE, ldap.SCOPE_SUBTREE, f'(&(objectClass={LDAP_OBJECTCLASS_GROUP})(CN={LDAP_GROUP_PREFIX}{group_name}))',
                         ['cn', 'managedBy'])
    if not r:
        connect.unbind()
        return None, []
    cn, entry = r[0]
    ldap_group_owner = entry['managedBy'][0].decode()
    ldap_group_users = connect.search_s(LDAP_BASE, ldap.SCOPE_SUBTREE, f'(&(objectClass={LDAP_OBJECTCLASS_USER})(memberOf={cn}))',
                                        ['cn', 'mail'])
    connect.unbind()
    return ldap_group_owner, ldap_group_users


@dramatiq.actor(priority=0, max_retries=3)
def group_create(group_name, group_id):
    ldap_group_owner, ldap_group_users = get_ldap_owner_with_users(group_name)
    ldap_emails = []
    for ldap_user in ldap_group_users:
        try:
            email = ldap_user[1]['mail'][0].decode().lower()
        except Exception as e:
            print(e)
            continue
        ldap_emails.append(email)
        access_level = gitlab.DEVELOPER_ACCESS
        if ldap_user[0] == ldap_group_owner:
            access_level = gitlab.OWNER_ACCESS
            # explicitly set the OWNER for next gitlab_remove_user_from_group
            gitlab_add_user_to_group(group_id, access_level, email)
            continue
        gitlab_add_user_to_group.send(group_id, access_level, email)
    # remove creator from group
    gitlab_remove_user_from_group.send(group_id, ldap_emails)


@dramatiq.actor(priority=10, max_retries=3)
def gitlab_add_user_to_group(group_id, access_level, email):
    gitlab_user = gl.users.list(search=email)
    if not gitlab_user:
        print(f'User {email} not found')
        return
    group = gl.groups.get(group_id)
    try:
        group.members.create({'user_id': gitlab_user[0].id,
                              'access_level': access_level})
        print(f'User {email} added to {group.name} access_level: {access_level}')
    except gitlab.exceptions.GitlabHttpError as e:
        print(f'User {email} not added to {group.name} ==> {e}')
    except gitlab.exceptions.GitlabCreateError as e:
        print(f'User {email} not added to {group.name} ==> {e}')


@dramatiq.actor(priority=0, max_retries=3)
def access_to_project(project_id):
    project = gl.projects.get(project_id)
    description = project.description
    if description is None:
        return
    for line in description.splitlines():
        ADD_LDAP_GROUP=[]
        RESTRICTED_LDAP_GROUP=[]
        for line in description.splitlines():
          if line.startswith(f'{ACCESS_PROJECT_STARTSWITH}{ACCESS_PROJECT_DELIMITER}') and  not RESTRICT_KEYWORD in line:
             group_name = re.compile(f'^{ACCESS_PROJECT_STARTSWITH}{ACCESS_PROJECT_DELIMITER}').sub('', line)
             ADD_LDAP_GROUP.append(group_name)
          if line.startswith(f'{ACCESS_PROJECT_STARTSWITH}{ACCESS_PROJECT_DELIMITER}')and  RESTRICT_KEYWORD in line:
             group_name = re.compile(f'^{ACCESS_PROJECT_STARTSWITH}{ACCESS_PROJECT_DELIMITER}|{RESTRICT_PROJECT_DELIMITER}{RESTRICT_KEYWORD}').sub('', line)
             RESTRICTED_LDAP_GROUP.append(group_name)
    users_to_add=[]
    for i in ADD_LDAP_GROUP:
      ldap_group_users = get_ldap_owner_with_users(i)
      for ldap_user in ldap_group_users:
            try:
                email = ldap_user [1]['mail'][0].decode().lower()
                users_to_add.append(email)
            except Exception as e:
                print(e)
    current_project_members=[]
    members = project.members.all(as_list=False, per_page=CRON_PROJECTS_PER_PAGE)
    for i in members:
      current_project_members.append(i.Mail)           

@dramatiq.actor(priority=10, max_retries=3)
def gitlab_add_user_to_project(project_id, access_level, email):
    gitlab_user = gl.users.list(search=email)
    if not gitlab_user:
        print(f'User {email} not found')
        return
    project = gl.projects.get(project_id)
    try:
        project.members.create({'user_id': gitlab_user[0].id,
                              'access_level': access_level})
        print(f'User {email} added to {project.name} access_level: {access_level}')
    except gitlab.exceptions.GitlabHttpError as e:
        print(f'User {email} not added to {project.name} ==> {e}')
    except gitlab.exceptions.GitlabCreateError as e:
        print(f'User {email} not added to {project.name} ==> {e}')

@dramatiq.actor(priority=10, max_retries=3)
def gitlab_remove_user_from_project(project_id, email):
    gitlab_user = gl.users.list(search=email)
    if not gitlab_user:
        print(f'User {email} not found')
        return
    project = gl.projects.get(project_id)
    try:
        project.members.delete({'user_id': gitlab_user[0].id)
        print(f'User {email} removed from {project.name}')
    except gitlab.exceptions.GitlabHttpError as e:
        print(f'User {email} not removed from {project.name} ==> {e}')

@dramatiq.actor(priority=10, max_retries=3)
def gitlab_remove_user_from_group(group_id, ldap_emails):
    gitlab_group = gl.groups.get(group_id)
    gitlab_members = gitlab_group.members.all(as_list=False, all=True)
    for member in gitlab_members:
        user = gl.users.get(member['id'])
        if not user.email:
            continue
        if user.email not in ldap_emails:
            gitlab_group.members.delete(member['id'])
            print(f'User {user.email} remove from {gitlab_group.name}')


@dramatiq.actor(priority=0, max_retries=3)
def gitlab_user_create(user_id):
    gitlab_user = gl.users.get(user_id)
    connect = ldap.initialize(LDAP_URL)
    connect.set_option(ldap.OPT_REFERRALS, 0)
    connect.simple_bind_s(LDAP_USER, LDAP_PASS)
    ldap_user = connect.search_s(LDAP_BASE, ldap.SCOPE_SUBTREE, f'(&(objectClass={LDAP_OBJECTCLASS_USER})(mail={gitlab_user.email}))',
                                            ['cn', 'mail', 'thumbnailPhoto'])
    connect.unbind()
    if not ldap_user:
        print(f'User {gitlab_user.email} not found in ldap')
        return
    if 'thumbnailPhoto' in ldap_user[0][1]:
        thumbnailPhoto = ldap_user[0][1]['thumbnailPhoto'][0]
        gitlab_user.avatar, width, height = resize_image(thumbnailPhoto)
        gitlab_user.save()
        print(f'set avatar {gitlab_user.email}')
    else:
        print(f'thumbnailPhoto not found for {gitlab_user.email}')


@dramatiq.actor(priority=20, max_retries=3)
def gitlab_sync_avatars(page):
    time.sleep(1)
    connect = ldap.initialize(LDAP_URL)
    connect.set_option(ldap.OPT_REFERRALS, 0)
    connect.simple_bind_s(LDAP_USER, LDAP_PASS)
    time.sleep(1)
    for user in gl.users.list(as_list=False, page=page, per_page=CRON_AVATARS_PER_PAGE, active=True):
        ldap_user = connect.search_s(LDAP_BASE, ldap.SCOPE_SUBTREE, f'(&(objectClass={LDAP_OBJECTCLASS_USER})(mail={user.email}))',
                                            ['cn', 'mail', 'thumbnailPhoto'])
        if not ldap_user:
            continue
        if 'thumbnailPhoto' in ldap_user[0][1]:
            thumbnailPhoto = ldap_user[0][1]['thumbnailPhoto'][0]
            user.avatar, width, height = resize_image(thumbnailPhoto)
            time.sleep(1)
            user.save()
            print(f'set avatar ({width}x{height}) {user.email}')
    connect.unbind()
