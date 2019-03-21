import os
from urllib.parse import urlparse

import ldap
import dramatiq
from dramatiq.brokers.redis import RedisBroker
import requests
import gitlab

redis_broker = RedisBroker(url=os.getenv("REDIS_URL", "redis://redis:6379/1"))
dramatiq.set_broker(redis_broker)

STATISTIC_URL = os.getenv("STATISTIC_URL", "http://statistic.com/post-receive")
LDAP_URL = os.getenv('LDAP_URL', 'ldap://company.com_by:12345')
LDAP_USER = os.getenv('LDAP_USER', 'Reader@company.com_by')
LDAP_PASS = os.getenv('LDAP_PASS', 'PASS')
LDAP_BASE = os.getenv('LDAP_BASE', 'DC=company,DC=com_by')
gl = gitlab.Gitlab(os.getenv('GITLAB_URL', 'https://gitlab.company.com_by'),
                   private_token=os.getenv('GITLAB_TOKEN', 'GITLAB_TOKEN'))

LDAP_GROUP_PREFIX = os.getenv('LDAP_GROUP_PREFIX', 'Group ')
LDAP_OBJECTCLASS_GROUP = os.getenv('LDAP_OBJECTCLASS_GROUP', 'group')
LDAP_OBJECTCLASS_USER = os.getenv('LDAP_OBJECTCLASS_USER', 'user')


def repo_convert_http_to_ssh(repo_url):
    split = repo_url.split('/commit/')
    repo = ''.join(split[:-1])
    parsed_uri = urlparse(repo)
    path = parsed_uri.path
    if path.startswith('/'):
        path = path[1:]
    return f'git@{parsed_uri.netloc}:{path}.git'


@dramatiq.actor(priority=0)
def statistic_prepare_data(commits):
    for commit in commits:
        data = {"id": commit['id'],
                "author_email": commit['author']['email'].lower(),
                "repository": repo_convert_http_to_ssh(commit['url']),
                "created_at": commit['timestamp']}
        statistic_push_data.send(data)


@dramatiq.actor(priority=10, max_retries=3)
def statistic_push_data(data):
    requests.post(STATISTIC_URL, data=data)
    print(f"commit {data['id']} by {data['author_email']} in {data['repository']}")


@dramatiq.actor(priority=10, max_retries=3)
def group_create(group_name, group_id):
    connect = ldap.initialize(LDAP_URL)
    connect.set_option(ldap.OPT_REFERRALS, 0)
    connect.simple_bind_s(LDAP_USER, LDAP_PASS)
    r = connect.search_s(LDAP_BASE, ldap.SCOPE_SUBTREE, f'(&(objectClass=group)(CN={LDAP_GROUP_PREFIX}{group_name}))',
                         ['cn', 'managedBy'])
    if not r:
        connect.unbind()
        return
    cn, entry = r[0]
    ldap_group_owner = entry['managedBy'][0].decode()
    ldap_group_users = connect.search_s(LDAP_BASE, ldap.SCOPE_SUBTREE, f'(&(objectClass=user)(memberOf={cn}))',
                                        ['cn', 'mail'])
    for ldap_user in ldap_group_users:
        email = ldap_user[1]['mail'][0].decode()
        access_level = gitlab.DEVELOPER_ACCESS
        if ldap_user[0] == ldap_group_owner:
            access_level = gitlab.OWNER_ACCESS
        gitlab_add_user_to_group.send(group_id, access_level, email)
    connect.unbind()


@dramatiq.actor(priority=20, max_retries=3)
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
