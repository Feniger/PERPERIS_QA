"""Explicit metadata maintenance. Never deletes ticket or audit history."""
import time


def prune(database):
    current = int(time.time())
    with database.connection(write=True) as db:
        sessions = db.execute('DELETE FROM qa_sessions WHERE expires_at<? OR revoked_at IS NOT NULL', (current,)).rowcount
        buckets = db.execute('DELETE FROM qa_auth_throttle WHERE window_start<?', (current-86400,)).rowcount
    return {'sessions': sessions, 'throttle_buckets': buckets}


def main():
    import argparse
    from database import from_env
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if not args.apply:
        parser.error('--apply is required')
    database=from_env()
    try:
        database.start()
        database.check_schema()
        print(prune(database))
    except Exception:
        parser.exit(1,'Metadata maintenance failed; check private configuration.\n')
    finally:
        database.close()


if __name__=='__main__':
    main()
