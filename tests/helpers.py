from pathlib import Path

from x_list_discovery.models import profile
from x_list_discovery.storage import write_json
from x_list_discovery.workspace import Workspace, initialize
from x_list_discovery import pipeline


def user(handle, uid, bio="Independent trader and researcher", followers=100, **extra):
    return profile({"userName": handle, "id": str(uid), "description": bio, "followers": followers,
                    "following": 20, **extra})


def fixture(root, name="demo", ready=True):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    members = root / (name + "-members.json")
    write_json(members, [user("Alice", 1), user("Bob", 2),
                         user("Project", 3, "The official account of our protocol")])
    excluded = root / (name + "-excluded.json")
    write_json(excluded, [user("OldOwnedName", 20)])
    ws = Workspace(root / "runs", name)
    initialize(ws, members, excluded=excluded, owner="Me", personal_seeds_only=True,
               individuals_only=True, exclude_bd=True)
    targets = [user("Alpha", 10, followers=1, name="=HYPERLINK(\"bad\")\x00"),
               user("Gamma", 11, followers=10000), user("Project", 3), user("RenamedOwned", 20),
               user("Me", 30), user("Org", 12, "Official account of the trading platform"),
               user("Mystery", 13, ""), user("BDPerson", 14, "Trader, business development"),
               user("BotPerson", 15, "Researcher", isAutomated=True)]
    graph = {"Alice": targets + [targets[0]], "Bob": targets}
    path = root / (name + "-graph.json")
    write_json(path, graph)
    if ready:
        pipeline.import_graph(ws, path)
        pipeline.aggregate(ws)
    return ws, graph, path
