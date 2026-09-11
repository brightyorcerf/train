from fastapi import FastAPI

app = FastAPI(title="VASP Attribution Engine (SIH26182)")


@app.get("/health")
def health():
    return {"status": "ok"}
