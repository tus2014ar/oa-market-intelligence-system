# Environment and packaging (DL-80)

## The supported baseline

- **Python 3.12.** CI and the Docker image use it. Newer Pythons work for local development (the author's machine runs 3.14), but 3.12 is what is tested and pinned.
- **`requirements.txt` is fully pinned** (about 140 packages): the exact set the green CI run of 28 Oct 2026 installed on Linux with Python 3.12. CI, the monthly pipeline workflow and the Docker image all install it, so each run gets the same packages.
- **`requirements.in`** is the short list of top-level packages (what a person edits). `pyproject.toml` lists the direct runtime dependencies with lower bounds, plus two optional groups, `notebooks` and `dev`.

## Set up from a clone

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # the pinned packages
pip install --no-deps -e .         # the project itself; no PYTHONPATH needed after this
```

The commands are then available by name:

| Command | Runs |
|---|---|
| `oa-pipeline` | build the warehouse from the raw extracts (`pipeline.py`) |
| `oa-publish` | build, check and publish the warehouse atomically (`publish.py`) |
| `oa-model` | the modelling runner (`--counts-only` or `--run`) |
| `oa-external` | the public-data loaders |

`python -m oa_market_intelligence.<module>` with `PYTHONPATH=src` still works. The code finds `data/` beside `src/`, so run from a clone (an editable install keeps that true); a copied wheel would not find the data.

## Docker

```bash
docker build -t oa-market-intelligence .
docker run --rm -v "$PWD/data:/app/data" oa-market-intelligence oa-publish
docker run --rm -v "$PWD/data:/app/data" oa-market-intelligence oa-model --counts-only
```

The image holds Python 3.12, the pinned packages and the code; the data is mounted from `data/` (the raw extracts are about 26 MB and the published databases about 15 MB). CI builds the image on every pull request and runs the commands' `--help`, so a broken Dockerfile or pin file fails there.

## Changing a dependency

1. Edit `requirements.in`.
2. Regenerate the pins on the baseline Python:
   ```bash
   docker run --rm -v "$PWD":/w -w /w python:3.12-slim sh -c "pip install -r requirements.in && pip freeze --exclude-editable > requirements.txt"
   ```
3. Re-add the two header comment lines at the top of `requirements.txt` and open a pull request; CI runs the whole suite on the new pins.

## The environment behind the stored modelling results

`data/reference/modelling_results.json` and the other result files were produced on the author's machine, not on the baseline: Python 3.14.6, numpy 2.5.2, pandas 3.0.5, scikit-learn 1.9.1, scipy 1.18.1, statsmodels 0.15.0, pyarrow 25.0.1, pandera 0.33.1, SQLAlchemy 2.0.54, joblib 1.6.0. The two runs that were compared (byte-identical) used that environment. The pinned baseline has slightly newer versions of some of these (pandas 3.1.0, numpy 2.5.4, SQLAlchemy 2.1.6, pandera 0.35.0, pyarrow 26.0.0), so a re-run of the full modelling run on the baseline is expected to give the same conclusions but is not guaranteed to be byte-identical to the stored file. The modelling tests, which use small synthetic data, pass on both. Whether the full run reproduces exactly under the pins has not been checked; it takes about two hours.
