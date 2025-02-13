import functools
import json
import os
from datetime import datetime
from typing import List

import dateutil.parser
import flask as f
import requests
import sqlalchemy as s

from ..entities._entity import (
    Base,
    EntityMixin,
    EntitySerializer,
    NotNull,
    Nullable,
    generate_uuid,
)


class Commit(Base, EntityMixin):
    __tablename__ = "commit"
    id = NotNull(s.String(50), primary_key=True, default=generate_uuid)
    sha = NotNull(s.String(50))
    branch = Nullable(s.String(510))
    fork_point_sha = Nullable(s.String(50))
    parent = Nullable(s.String(50))
    repository = NotNull(s.String(100))
    message = NotNull(s.String(250))
    author_name = NotNull(s.String(100))
    author_login = Nullable(s.String(50))
    author_avatar = Nullable(s.String(100))
    timestamp = Nullable(s.DateTime(timezone=False))

    def get_parent_commit(self):
        return Commit.first(sha=self.parent, repository=self.repository)

    @staticmethod
    def create_no_context():
        commit = Commit.first(sha="", repository="")
        if not commit:
            commit = Commit.create(
                {
                    "sha": "",
                    "repository": "",
                    "parent": None,
                    "timestamp": None,
                    "message": "",
                    "author_name": "",
                }
            )
        return commit

    @staticmethod
    def create_unknown_context(sha, repository):
        return Commit.create(
            {
                "sha": sha,
                "repository": repository,
                "parent": None,
                "timestamp": None,
                "message": "",
                "author_name": "",
            }
        )

    @staticmethod
    def create_github_context(sha, repository: str, github: dict):
        return Commit.create(
            {
                "sha": sha,
                "branch": github["branch"],
                "fork_point_sha": github["fork_point_sha"],
                "repository": repository,
                "parent": github["parent"],
                "timestamp": github["date"],
                "message": github["message"],
                "author_name": github["author_name"],
                "author_login": github["author_login"],
                "author_avatar": github["author_avatar"],
            }
        )


# NB: this assumes only one branch will be associated with a SHA when posting to
# Conbench. Subsequent posts with the same SHA on a new branch will fail.
# That should be impossible with a "normal" CI setup, and only possible if people post
# manually outside of CI.
#
# We impose this limitation here to make history queries easier to manage.
s.Index(
    "commit_index",
    Commit.sha,
    Commit.repository,
    unique=True,
)


class _Serializer(EntitySerializer):
    def _dump(self, commit):
        url = None
        if commit.repository and commit.sha:
            url = f"{commit.repository}/commit/{commit.sha}"
        timestamp = commit.timestamp.isoformat() if commit.timestamp else None
        result = {
            "id": commit.id,
            "sha": commit.sha,
            "branch": commit.branch,
            "fork_point_sha": commit.fork_point_sha,
            "url": url,
            "parent_sha": commit.parent,
            "repository": commit.repository,
            "message": commit.message,
            "author_name": commit.author_name,
            "author_login": commit.author_login,
            "author_avatar": commit.author_avatar,
            "timestamp": timestamp,
            "links": {
                "list": f.url_for("api.commits", _external=True),
                "self": f.url_for("api.commit", commit_id=commit.id, _external=True),
            },
        }
        if not self.many:
            parent, parent_url = commit.get_parent_commit(), None
            if parent:
                parent_url = f.url_for(
                    "api.commit", commit_id=parent.id, _external=True
                )
            result["links"]["parent"] = parent_url
        return result


class CommitSerializer:
    one = _Serializer()
    many = _Serializer(many=True)


GITHUB = "https://api.github.com"
this_dir = os.path.abspath(os.path.dirname(__file__))


def repository_to_name(repository):
    if not repository:
        return ""
    name = repository
    if "github.com/" in repository:
        name = repository.split("github.com/")[1]
    elif "git@github.com:" in repository:
        name = repository.split("git@github.com:")[1]
    return name


def repository_to_url(repository):
    name = repository_to_name(repository)
    return f"https://github.com/{name.lower()}" if name else ""


def get_github_commit(repository: str, pr_number: str, branch: str, sha: str) -> dict:
    if not repository or not sha:
        return {}

    github = GitHub()
    name = repository_to_name(repository)
    commit = github.get_commit(name, sha)
    if commit is None:
        return {}

    if branch:
        commit["branch"] = branch
    elif pr_number:
        commit["branch"] = github.get_branch_from_pr_number(
            name=name, pr_number=pr_number
        )
    else:
        commit["branch"] = github.get_default_branch(name=name)

    commit["fork_point_sha"] = github.get_fork_point_sha(name=name, sha=sha)

    return commit


def backfill_default_branch_commits(
    repository: str, new_commit: Commit
) -> List[Commit]:
    """Catches up the default-branch commits in the database.

    Will search GitHub for any untracked commits, between the given new_commit back in
    time to the last tracked commit, and backfill all of them.

    Won't backfill any commits before the last tracked commit. But if there are no
    commits in the database, will backfill them all.
    """
    github = GitHub()
    name = repository_to_name(repository)
    default_branch = github.get_default_branch(name)

    last_tracked_commit = Commit.all(
        filter_args=[Commit.sha != new_commit.sha, Commit.timestamp.isnot(None)],
        branch=default_branch,
        repository=repository,
        order_by=Commit.timestamp.desc(),
        limit=1,
    )

    if last_tracked_commit:
        since = last_tracked_commit[0].timestamp
    elif os.getenv("DB_HOST") == "localhost":
        print(
            "Your DB_HOST is localhost, so assuming you're running "
            "conbench/tests/populate_local_conbench.py. Backfilling the DB only from "
            "2022-01-01 in order to save time."
        )
        since = datetime(2022, 1, 1)
    else:
        since = datetime(1970, 1, 1)

    commits = github.get_commits_to_branch(
        name=name,
        branch=default_branch,
        since=since,
        until=new_commit.timestamp,
    )
    commits_to_try = commits[1:-1]  # since/until are inclusive; we want exclusive
    backfilled_commits = []

    print(f"Backfilling {len(commits_to_try)} commit(s)")
    for commit_info in commits_to_try:
        commit_info["github"]["branch"] = default_branch
        commit_info["github"]["fork_point_sha"] = commit_info["sha"]
        try:
            commit = Commit.create_github_context(**commit_info)
            backfilled_commits.append(commit)
        except s.exc.IntegrityError as e:
            if "commit_index" in str(e):
                print(f"Commit {commit_info['sha']} already existed in the database")
            else:
                raise

    return backfilled_commits


class GitHub:
    def __init__(self):
        self.test_shas = {
            "02addad336ba19a654f9c857ede546331be7b631": "github_child.json",
            "4beb514d071c9beec69b8917b5265e77ade22fb3": "github_parent.json",
            "6d703c4c7b15be630af48d5e9ef61628751674b2": "github_grandparent.json",
            "81e9417eb68171e03a304097ae86e1fd83307130": "github_elder.json",
        }
        self.test_commits = [
            "02addad336ba19a654f9c857ede546331be7b631",
            "4beb514d071c9beec69b8917b5265e77ade22fb3",
            "6d703c4c7b15be630af48d5e9ef61628751674b2",
            "81e9417eb68171e03a304097ae86e1fd83307130",
            "4de992c60ba433ad9b15ca1c41e6ec40bc542c2a",
            "unknown commit",
            "testing repository with just org/repo",
            "testing repository with git@g",
        ]

    def get_default_branch(self, name):
        if name == "org/repo":
            # test case
            return "org:default_branch"

        url = f"{GITHUB}/repos/{name}"
        response = self._get_response(url)
        if not response:
            return None

        if response["fork"]:
            org = response["source"]["owner"]["login"]
            branch = response["source"]["default_branch"]
        else:
            org = response["owner"]["login"]
            branch = response["default_branch"]

        return f"{org}:{branch}"

    def get_commit(self, name, sha):
        if sha in self.test_commits:
            response = self.test_commit(sha)
        else:
            url = f"{GITHUB}/repos/{name}/commits/{sha}"
            response = self._get_response(url)
        return self._parse_commit(response) if response else None

    def get_commits_to_branch(
        self, name: str, branch: str, since: datetime, until: datetime
    ) -> List[dict]:
        """Get information about each commit on a given branch.

        since and until are inclusive.
        """
        if name == "org/repo":
            # test case
            return []

        if ":" in branch:
            branch = branch.split(":")[1]
        since = since.replace(tzinfo=None).isoformat() + "Z"
        until = until.replace(tzinfo=None).isoformat() + "Z"

        print(
            f"Finding all commits to the {branch} branch of {name} between {since} and "
            f"{until}"
        )
        url = (
            f"{GITHUB}/repos/{name}/commits?per_page=100&sha={branch}"
            f"&since={since}&until={until}"
        )
        commits = []
        page = 1

        this_page = self._get_response(url + f"&page={page}")
        if not this_page:
            print("API request failed")
            return []
        commits += this_page

        while len(this_page) == 100:
            page += 1
            this_page = self._get_response(url + f"&page={page}")
            commits += this_page

        return [
            {
                "sha": commit["sha"],
                "repository": repository_to_url(name),
                "github": self._parse_commit(commit),
            }
            for commit in commits
        ]

    def get_fork_point_sha(self, name: str, sha: str) -> str:
        """
        Get the most common ancestor commit between an arbitrary SHA and the default
        branch.

        Returns ``None`` if sha is not supplied or if GitHub can't find it, otherwise
        returns the fork point sha, called the "merge base" in git-speak.
        """
        if sha in self.test_commits:
            return "some_fork_point_sha"

        if not name or not sha:
            return None

        base = self.get_default_branch(name=name)
        url = f"{GITHUB}/repos/{name}/compare/{base}...{sha}"
        response = self._get_response(url=url)
        if not response:
            return None

        fork_point_sha = response["merge_base_commit"]["sha"]
        return fork_point_sha

    def get_branch_from_pr_number(self, name: str, pr_number: str) -> str:
        if pr_number == 12345678:
            # test case
            return "some_user_or_org:some_branch"

        if not name or not pr_number:
            return None

        url = f"{GITHUB}/repos/{name}/pulls/{pr_number}"
        response = self._get_response(url=url)
        if not response:
            return None

        branch = response["head"]["label"]
        return branch

    @functools.cached_property
    def session(self):
        token, session = os.getenv("GITHUB_API_TOKEN"), None
        if token:
            session = requests.Session()
            session.headers = {"Authorization": f"Bearer {token}"}
        return session

    def test_commit(self, sha):
        if "unknown" in sha or "testing" in sha:
            return None
        fixture = f"../tests/entities/{self.test_shas[sha]}"
        path = os.path.join(this_dir, fixture)
        with open(path) as fixture:
            return json.load(fixture)

    @staticmethod
    def _parse_commits(commits):
        return [commit["sha"] for commit in commits]

    @staticmethod
    def _parse_commit(commit):
        author = commit.get("author")
        commit_author = commit["commit"]["author"]
        return {
            "parent": commit["parents"][0]["sha"] if commit["parents"] else None,
            "date": dateutil.parser.isoparse(commit_author["date"]),
            "message": commit["commit"]["message"].split("\n")[0],
            "author_name": commit_author["name"],
            "author_login": author["login"] if author else None,
            "author_avatar": author["avatar_url"] if author else None,
        }

    def _get_response(self, url):
        response = self.session.get(url) if self.session else requests.get(url)
        if response.status_code != 200:
            print(response.json())
            return None
        return response.json()
