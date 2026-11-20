# Paper Plane

**Send it flying.** A chat app where messages arrive the moment they are sent.

Paper Plane is being built in stages (see [docs/PLAN.md](docs/PLAN.md)). The full README, with pictures of every page, comes in the last stage.

## Run It on Your Computer

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\flask --app paperplane upgrade-db
.venv\Scripts\flask --app paperplane run --debug --port 5015
```

Then open http://127.0.0.1:5015.
