import os
import re
import gitlab
import datetime

from japronto import Application
from tasks import statistic_prepare_data, group_create, gitlab_user_create, access_to_project, CRON_PROJECTS_PER_PAGE, \
    ACCESS_PROJECT_STARTSWITH, ACCESS_PROJECT_DELIMITER, GITLAB_URL

TOKEN = os.getenv("TOKEN", "Qwerty")
app = Application()

STATISTIC_URL = os.getenv("STATISTIC_URL")
IGNORE_EVENTS = os.getenv("IGNORE_EVENTS", "").split(',')
GET_ALL_ACTIVE_PROJECTS = os.getenv("GET_ALL_ACTIVE_PROJECTS", "/get_all_projects")
GET_ALL_ACTIVE_PROJECTS_DAYS = int(os.getenv("GET_ALL_ACTIVE_PROJECTS_DAYS", "30"))


async def filter_hooks(request):
    # validate hook
    if request.headers['X-Gitlab-Token'] != TOKEN:
        return request.Response(code=401, text="Please use valid X-Gitlab-Token")

    data = request.json
    # filter tasks
    print(f"Event name {data['event_name']}")
    if data['event_name'] in IGNORE_EVENTS:
        return request.Response(text="event ignore")
    if data['event_name'] == "repository_update":
        print(f"User {data['user_name']} push to {data['project']['git_ssh_url']}")
        statistic_prepare_data.send(data['project_id'], data['project']['git_ssh_url'], data['changes'])
    if data['event_name'] in ["project_update", "project_create"]:
        print(f"Projects {data['path_with_namespace']} create/update")
        access_to_project.send(data['project_id'])
    if data['event_name'] == "user_create":
        print(f"Created user {data['email']}, set avatar start add groups")
        gitlab_user_create.send(data['user_id'])
    if data['event_name'] == "group_create":
        locations = data['full_path'].split('/')
        if len(locations) >= 2:
            return request.Response(text="OK")
        print(f"Create group {data['name']} path {locations[0]}")
        group_create.send(locations[0].upper(), data['group_id'])
    return request.Response(text="OK")


async def get_info(request):
    return request.Response(
        text=f"- Push commits in queue to {STATISTIC_URL}\n"
             "- Auto add users(developers) with owner to new gitlab group\n"
             "Use POST with X-Gitlab-Token and X-Gitlab-Event headers"
    )


async def get_all_active_projects(request):
    data = []
    date_now = datetime.datetime.now() - datetime.timedelta(days=GET_ALL_ACTIVE_PROJECTS_DAYS)
    if 'private_token' not in request.query.keys():
        return request.Response(json=data)
    gl = gitlab.Gitlab(GITLAB_URL, private_token=request.query['private_token'])
    projects = gl.projects.list(as_list=False, per_page=100, order_by='last_activity_at', sort='desc', simple='true')
    for project in projects:
        if date_now > datetime.datetime.strptime(project.last_activity_at, "%Y-%m-%dT%H:%M:%S.%fZ"):
            break
        group_name = ""
        description = project.description
        if description is not None:
            for line in description.splitlines():
                if line.startswith(f'{ACCESS_PROJECT_STARTSWITH}{ACCESS_PROJECT_DELIMITER}'):
                    group_name = re.compile(f'^{ACCESS_PROJECT_STARTSWITH}{ACCESS_PROJECT_DELIMITER}').sub('', line)
                    # exist and use first group code
                    break
        if group_name == "" and project.namespace['kind'] == 'group':
            locations = project.namespace['full_path'].split('/')
            if len(locations) >= 1:
                group_name = locations[0]
        if group_name != "":
            data.append({ACCESS_PROJECT_STARTSWITH.lower(): group_name.lower(), "path_with_namespace": project.path_with_namespace})
    return request.Response(json=data)


r = app.router
r.add_route('/', get_info, methods=["GET"])
r.add_route('/', filter_hooks, methods=["POST"])
r.add_route(GET_ALL_ACTIVE_PROJECTS, get_all_active_projects, methods=["GET"])

app.run()
