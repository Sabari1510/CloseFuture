"""Apply migrations/*.sql to DATABASE_URL."""
import pathlib
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "config")
import psycopg
from settings import settings
from src.app.db.pool import quoted_dsn


def main() -> None:
    assert settings.database_url, "DATABASE_URL missing"
    files = sorted(pathlib.Path("migrations").glob("*.sql"))
    with psycopg.connect(quoted_dsn()) as conn:
        for f in files:
            print(f"applying {f}")
            conn.execute(f.read_text())
        conn.commit()
    print("migrations ok")


if __name__ == "__main__":
    main()
