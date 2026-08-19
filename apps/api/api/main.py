from fastapi import FastAPI

from api import __version__

app = FastAPI(title="Trident Oracle API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
