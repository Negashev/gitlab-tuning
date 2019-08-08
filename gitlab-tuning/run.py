import os

from japronto import Application
from tasks import statistic_prepare_data, group_create, gitlab_user_create

TOKEN = os.getenv("TOKEN", "Qwerty")
app = Application()

STATISTIC_URL = os.getenv("STATISTIC_URL")
IGNORE_EVENTS = os.getenv("IGNORE_EVENTS", "").split(',')


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
    if data['event_name'] == "user_create":
        print(f"Created user {data['email']}, set avatar start add groups")
        gitlab_user_create.send(data['id'])
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


r = app.router
r.add_route('/', get_info, methods=["GET"])
r.add_route('/', filter_hooks, methods=["POST"])

app.run()
