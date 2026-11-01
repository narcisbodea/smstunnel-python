# Publishing `smstunnel` to PyPI

The package is published by GitHub Actions with **PyPI Trusted Publishing**: PyPI trusts the workflow
`.github/workflows/publish.yml` in the repository `narcisbodea/smstunnel-python`, in the GitHub environment `pypi`.
There is no API token or password anywhere: not in GitHub secrets, not on a computer. Each run gets a short-lived
token from GitHub (OIDC) that PyPI accepts only from that workflow. The upload also gets signed attestations
(PEP 740) automatically.

The name `smstunnel` was free on PyPI on 1 November 2026 (`https://pypi.org/pypi/smstunnel/json` answered 404).
A pending publisher does **not** reserve it: if someone else uploads a project called `smstunnel` first, the
pending publisher stops working. So do steps 1 to 3 and the first release close together.

## 1. GitHub: the repository and the `pypi` environment (once)

1. Create the public repository `narcisbodea/smstunnel-python` and push this folder to its `main` branch.
2. In the repository, open **Settings → Environments → New environment**, name it `pypi`, and save.
3. Recommended, in the same environment:
   - **Required reviewers**: add yourself. Every publish then waits for your click on **Review deployments → Approve**.
   - **Deployment branches and tags → Selected branches and tags → Add rule**: tag pattern `v*`.

## 2. PyPI: the account (once)

1. Create an account at <https://pypi.org/account/register/> and confirm the email address.
2. Turn on two-factor authentication (PyPI requires it to publish): **Account settings → Two factor authentication**,
   with an authenticator app or a security key (Windows Hello works). Keep the recovery codes.

## 3. PyPI: the pending publisher (once)

1. Open <https://pypi.org/manage/account/publishing/> (**Your account → Publishing**).
2. Under **Add a new pending publisher**, choose **GitHub** and fill in exactly:

   | Field | Value |
   | --- | --- |
   | PyPI Project Name | `smstunnel` |
   | Owner | `narcisbodea` |
   | Repository name | `smstunnel-python` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

3. Click **Add**. After the first successful upload it becomes the project's normal trusted publisher; nothing else
   to set up for later releases.

## 4. Release

1. Make sure the version in `src/smstunnel/_version.py` and the top of `CHANGELOG.md` are right (1.0.0 now).
2. On GitHub: **Releases → Draft a new release → Choose a tag**, type `v1.0.0` (create it on `main`), title
   `1.0.0`, paste the changelog entry, and **Publish release**.
3. The **Publish** workflow runs: tests, lint, types, a check that the tag matches the version, the build, and
   `twine check`. Then the **Publish to PyPI** job waits for your approval (if you set reviewers) and uploads.
4. Check <https://pypi.org/project/smstunnel/> and try `pip install smstunnel` in a fresh virtual environment.

For the next versions: change `_version.py` and `CHANGELOG.md`, commit, and publish a release tagged `v<version>`.
A version can never be uploaded twice, so a fix always needs a new version number.

## Optional: a dry run on TestPyPI first

TestPyPI is a separate site with a separate account.

1. Create an account at <https://test.pypi.org/account/register/> and add a pending publisher there with the same
   values, except **Environment name** `testpypi`.
2. In GitHub, add a second environment named `testpypi`.
3. In the repository: **Actions → Publish → Run workflow** (on `main`). It runs the same checks and uploads to
   <https://test.pypi.org/project/smstunnel/>.
4. Try it: `pip install --index-url https://test.pypi.org/simple/ smstunnel`.

## If the upload is refused

- `invalid-publisher`: the owner, repository, workflow file name or environment name on PyPI differs from the run.
  They must match exactly (`publish.yml`, not the workflow's display name).
- `File already exists`: that version is already on PyPI. Raise the version.
- The **Publish to PyPI** job never starts: approve the deployment, or check the environment's tag rule.

## What stays manual

Only the steps above: creating the accounts, adding the pending publisher, creating the GitHub repository and
publishing a release. Nothing in this repository needs a secret.
