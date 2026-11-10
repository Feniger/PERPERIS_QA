"""Bounded ASGI entrypoint. Cloud deployment remains explicitly blocked in app."""
import os


def settings():
    try:
        port = int(os.getenv('PORT', '8080'))
        concurrency = int(os.getenv('PERPERIS_QA_CONCURRENCY', '4'))
    except ValueError:
        raise ValueError('Invalid port or concurrency setting') from None
    if not 1 <= port <= 65535 or not 1 <= concurrency <= 32:
        raise ValueError('Invalid port or concurrency setting')
    return port, concurrency


def main():
    import uvicorn
    port, concurrency = settings()
    uvicorn.run('app:APP', host='0.0.0.0', port=port, lifespan='on',
                proxy_headers=False, access_log=False, limit_concurrency=concurrency,
                timeout_keep_alive=5)


if __name__ == '__main__':
    main()
