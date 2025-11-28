import os

import psycopg
from psycopg import sql
from dotenv import load_dotenv


def main():
    # Carrega o .env.appdb.local
    load_dotenv(".env.appdb.local")

    pg_host = os.getenv("PG_HOST", "localhost")
    pg_port = os.getenv("PG_PORT", "5432")
    pg_superuser = os.getenv("PG_SUPERUSER")
    pg_superpass = os.getenv("PG_SUPERPASS")

    app_db_name = os.getenv("APP_DB_NAME")
    app_db_user = os.getenv("APP_DB_USER")
    app_db_password = os.getenv("APP_DB_PASSWORD")

    if not all([pg_superuser, pg_superpass, app_db_name, app_db_user, app_db_password]):
        raise RuntimeError("Variáveis de ambiente faltando no .env.appdb.local")

    print("🔌 Conectando ao PostgreSQL como superuser…")

    conn = psycopg.connect(
        host=pg_host,
        port=pg_port,
        user=pg_superuser,
        password=pg_superpass,
        dbname="postgres",
        autocommit=True,
    )
    cur = conn.cursor()

    # --- Cria DB se não existir ---
    print(f"📦 Criando database '{app_db_name}' (se não existir)…")
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (app_db_name,))
    if cur.fetchone() is None:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(app_db_name)))
        print("   ✔ Database criado.")
    else:
        print("   ✔ Database já existe.")

    # --- Cria usuário se não existir / atualiza senha ---
    print(f"👤 Criando usuário '{app_db_user}' (se não existir)…")
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s;", (app_db_user,))
    if cur.fetchone() is None:
        # psycopg3: DDL não aceita parâmetros tipo %s → usar sql.Literal
        cur.execute(
            sql.SQL("CREATE USER {} WITH PASSWORD {}").format(
                sql.Identifier(app_db_user),
                sql.Literal(app_db_password),
            )
        )
        print("   ✔ Usuário criado.")
    else:
        print("   ✔ Usuário já existe, atualizando senha…")
        cur.execute(
            sql.SQL("ALTER USER {} WITH PASSWORD {}").format(
                sql.Identifier(app_db_user),
                sql.Literal(app_db_password),
            )
        )

    print("🔐 Ajustando privilégios no DATABASE…")
    cur.execute(
        sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
            sql.Identifier(app_db_name),
            sql.Identifier(app_db_user),
        )
    )

    cur.close()
    conn.close()

    # --- Agora ajusta o schema public do banco da aplicação ---
    print("🏗  Ajustando permissões no schema public do DB da aplicação…")

    conn_app = psycopg.connect(
        host=pg_host,
        port=pg_port,
        user=pg_superuser,
        password=pg_superpass,
        dbname=app_db_name,
        autocommit=True,
    )
    cur_app = conn_app.cursor()

    # Permissão para criar objetos no schema public
    cur_app.execute(
        sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
            sql.Identifier(app_db_user)
        )
    )

    # (Opcional, mas deixa tudo “pertencendo” ao user do app)
    cur_app.execute(
        sql.SQL("ALTER SCHEMA public OWNER TO {}").format(
            sql.Identifier(app_db_user)
        )
    )

    cur_app.close()
    conn_app.close()

    print("\n✅ Banco da aplicação e permissões configurados com sucesso!")


if __name__ == "__main__":
    main()
