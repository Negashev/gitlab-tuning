import os

import ldap
import dramatiq
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.brokers.redis import RedisBroker
from periodiq import PeriodicMiddleWare, cron
import requests
import gitlab


from tools import resize_image
REDIS_URL = os.getenv("REDIS_URL", None)
if REDIS_URL in [None, ""]:
    broker = RabbitmqBroker(url=os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672"))
else:
    broker = RedisBroker(url=REDIS_URL)
dramatiq.set_broker(broker)
broker.add_middleware(PeriodicMiddleWare())

STATISTIC_URL = os.getenv("STATISTIC_URL", None)
LDAP_URL = os.getenv('LDAP_URL', 'ldap://company.com_by:12345')
LDAP_USER = os.getenv('LDAP_USER', 'Reader@company.com_by')
LDAP_PASS = os.getenv('LDAP_PASS', 'PASS')
LDAP_BASE = os.getenv('LDAP_BASE', 'DC=company,DC=com_by')
gl = gitlab.Gitlab(os.getenv('GITLAB_URL', 'https://gitlab.company.com_by'),
                   private_token=os.getenv('GITLAB_TOKEN', 'GITLAB_TOKEN'))

tmp_LDAP_GROUP_PREFIX = os.getenv('LDAP_GROUP_PREFIX', 'Group')
if tmp_LDAP_GROUP_PREFIX:
    LDAP_GROUP_PREFIX = tmp_LDAP_GROUP_PREFIX + ' '
else:
    LDAP_GROUP_PREFIX = ''
LDAP_OBJECTCLASS_GROUP = os.getenv('LDAP_OBJECTCLASS_GROUP', 'group')
LDAP_OBJECTCLASS_USER = os.getenv('LDAP_OBJECTCLASS_USER', 'user')
CRON_SYNC_AVATARS = os.getenv('CRON_SYNC_AVATARS', '0 0 * * *')


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
            statistic_prepare_data.send(project_id, git_ssh_url, [change])


@dramatiq.actor(priority=20, max_retries=3)
def statistic_push_data(data):
    requests.post(STATISTIC_URL, data=data)
    print(f"commit {data['id']} by {data['author_email']} in {data['repository']}")


def get_ldap_owner_with_users(group_name):
    connect = ldap.initialize(LDAP_URL)
    connect.set_option(ldap.OPT_REFERRALS, 0)
    connect.simple_bind_s(LDAP_USER, LDAP_PASS)
    r = connect.search_s(LDAP_BASE, ldap.SCOPE_SUBTREE, f'(&(objectClass={LDAP_OBJECTCLASS_GROUP})(CN={LDAP_GROUP_PREFIX}{group_name}))',
                         ['cn', 'managedBy'])
    if not r:
        connect.unbind()
        return
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
        email = ldap_user[1]['mail'][0].decode().lower()
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


@dramatiq.actor(priority=10, max_retries=3)
def gitlab_remove_user_from_group(group_id, ldap_emails):
    gitlab_group = gl.groups.get(group_id)
    gitlab_members = gitlab_group.members.all(all=True)
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
        print(f'User {user.email} not found in ldap')
        return
    if 'thumbnailPhoto' in ldap_user[0][1]:
        thumbnailPhoto = ldap_user[0][1]['thumbnailPhoto'][0]
        gitlab_user.avatar = resize_image(thumbnailPhoto)
        gitlab_user.save()
        print(f'set avatar {user.email}')
    else:
        print(f'thumbnailPhoto not found for {user.email}')

@dramatiq.actor(periodic=cron(CRON_SYNC_AVATARS))
def gitlab_sync_avatars_prepare():
    page = 1
    while True:
        users = gl.users.list(page=page, per_page=10, active=True)
        if len(users)==0:
            break
        gitlab_sync_avatars.send(page)
        page += 1

@dramatiq.actor(priority=20, max_retries=3)
def gitlab_sync_avatars(page):
    connect = ldap.initialize(LDAP_URL)
    connect.set_option(ldap.OPT_REFERRALS, 0)
    connect.simple_bind_s(LDAP_USER, LDAP_PASS)
    for user in gl.users.list(page=page, per_page=10, active=True):
        ldap_user = connect.search_s(LDAP_BASE, ldap.SCOPE_SUBTREE, f'(&(objectClass={LDAP_OBJECTCLASS_USER})(mail={user.email}))',
                                            ['cn', 'mail', 'thumbnailPhoto'])
        if not ldap_user:
            continue
        if 'thumbnailPhoto' in ldap_user[0][1]:
            thumbnailPhoto = ldap_user[0][1]['thumbnailPhoto'][0]
            user.avatar = resize_image(thumbnailPhoto)
            user.save()
            print(f'set avatar {user.email}')
    connect.unbind()
