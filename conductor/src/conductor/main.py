from fastapi import FastAPI

app = FastAPI(title="conductor", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    import uvicorn

    uvicorn.run("conductor.main:app", host="0.0.0.0", port=8420, reload=True)


if __name__ == "__main__":
    main()
