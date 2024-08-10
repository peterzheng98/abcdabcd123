import docker
import os
import uuid
import logging
import sys
import time
import queue
import multiprocessing
import re
import subprocess
import shutil
import datetime
import redis

from DockerHelper import *
from SetupConstant import *
from MySQLHelper import *
from CompilePhase import *
from SemanticPhase import *

# set logging with date time, and both write to files
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    filename=log_path,
                    filemode='a')

GlobalName = sys.argv[1]

r = redis.Redis(host=BASE_HOST, port=6379, decode_responses=True)
mysql_connection = create_mysql_connection(host_name="", user_name="", user_password="", db_name="compiler")

def get_stuid_image_repo(r):
    if r.llen('judge') == 0:
        logging.info('no element in judge')
        return None, None, None
    element = r.lpop('judge') # pop from what?
    elements = element.split('|')
    stuid = elements[0]
    image_id = elements[1]
    repo_url = elements[2]
    logging.info('receive judge request {} {} {}'.format(stuid, image_id, repo_url))
    return stuid, image_id, repo_url

def process(mysql_connection, redis_handler):
    stuid, image_id, repo_url = get_stuid_image_repo(redis_handler)
    if stuid is None:
        return False
    uid = select_uid_from_stuid(connection=mysql_connection, stuid=stuid)
    if uid is None:
        return False
    attempt_id, submit_timestamp = create_a_submit(connection=mysql_connection, uid=uid, stuid=stuid, git_repo=repo_url)
    result, compile_id = run_compile(mysql_connection, attempt_id, uid, repo_url, image_id, submit_timestamp)
    if not result:
        logging.info('User {} failed at stage build'.format(stuid))
        return False
    logging.info('User {} success at stage build'.format(stuid))
    result, _ = run_semantic(mysql_connection, attempt_id, uid, repo_url, image_id, submit_timestamp, compile_id)
    if not result:
        logging.info('User {} failed at stage 1 semantic'.format(stuid))
        update_submit_status(mysql_connection, attempt_id, 'status', 5)
        return False
    logging.info('User {} success at stage 1 semantic'.format(stuid))
    update_submit_status(mysql_connection, attempt_id, 'status', 6)
    result, _ = run_semantic(mysql_connection, attempt_id, uid, repo_url, image_id, submit_timestamp, compile_id, isExtend=True)
    if not result:
        logging.info('User {} failed at stage 2 semantic'.format(stuid))
        update_submit_status(mysql_connection, attempt_id, 'status', 7)
        return False
    logging.info('User {} success at stage 2 semantic'.format(stuid))
    update_submit_status(mysql_connection, attempt_id, 'status', 8)
    


if __name__ == '__main__':
    while True:
        process(mysql_connection, r)
        time.sleep(1)