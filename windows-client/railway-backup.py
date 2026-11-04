"""Takes a Railway volume backup with the Railway CLI's own login, and confirms it appeared at about the volume's size. Never prints the token. Used by deploy-m3.ps1.
Railway's listing is the only check available from here: a restore replaces the live volume, so a backup can't be restore-tested without downtime."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

PROJECT = "c2a48c3b-60de-4eec-80a3-1e7cf5ff3e26"
cfg = json.load(open(os.path.expanduser("~/.railway/config.json")))
token = cfg["user"].get("accessToken") or cfg["user"].get("token")


def gql(query, variables=None):
    req = urllib.request.Request("https://backboard.railway.com/graphql/v2", data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "User-Agent": "railway-cli"})
    try:
        reply = json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        sys.exit(f"Railway refused: {e.code}. Run: npx @railway/cli login")
    if reply.get("errors"):
        sys.exit("Railway error: " + reply["errors"][0].get("message", "")[:200])
    return reply["data"]


data = gql("query($id:String!){project(id:$id){volumes{edges{node{name volumeInstances{edges{node{id currentSizeMB}}}}}}}}", {"id": PROJECT})
instances = [i["node"] for v in data["project"]["volumes"]["edges"] for i in v["node"]["volumeInstances"]["edges"]]
if len(instances) != 1:
    sys.exit(f"Expected one volume, found {len(instances)}")
volume = instances[0]
LIST = "query($id:String!){volumeInstanceBackupList(volumeInstanceId:$id){id createdAt referencedMB}}"


def backups():
    return sorted(gql(LIST, {"id": volume["id"]})["volumeInstanceBackupList"], key=lambda b: b["createdAt"], reverse=True)


before = {b["id"] for b in backups()}
if len(sys.argv) > 1 and sys.argv[1] == "create":
    gql("mutation($id:String!){volumeInstanceBackupCreate(volumeInstanceId:$id){workflowId}}", {"id": volume["id"]})
    for _ in range(40):
        new = [b for b in backups() if b["id"] not in before]
        if new and new[0].get("referencedMB"):
            size = new[0]["referencedMB"]
            if size < volume["currentSizeMB"] * 0.9:
                sys.exit(f"Backup is {size} MB but the volume is {volume['currentSizeMB']:.0f} MB")
            print(f"Backup {new[0]['id'][:8]} confirmed: {new[0]['createdAt']}, {size} MB of a {volume['currentSizeMB']:.0f} MB volume")
            sys.exit(0)
        time.sleep(10)
    sys.exit("The backup didn't appear in time")
for b in backups()[:5]:
    print(b["id"][:8], b["createdAt"], b.get("referencedMB"), "MB")
