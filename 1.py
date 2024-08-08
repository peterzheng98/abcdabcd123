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

GlobalName = sys.argv[2]

    
    # # semantic_testcases = open(semantic_check_lib, 'r').readlines()
    # semantic_ans = []
    # semantic_testcases = fetch_testcase_by_phase(connection, 1)

    # for testcase in semantic_testcases:
    #     p = multiprocessing.Process(target=run_semantic, args=(1, compile_path, user_id + ':latest', q, user_id, url, testcase, attempt_id, submit_timestamp, connection))
    #     p.start()
    #     p.join(15)
    #     if p.is_alive():
    #         p.terminate()
    #         p.join()
    #         logging.error('run semantic check timeout {} (request {}, url {})'.format(testcase, user_id, url))
    #         semantic_ans.append((False, 'TLE'))
    #     else:
    #         result = q.get()
    #         if result[0] != 0:
    #             logging.error('run semantic check failed (request {}, url {})'.format(user_id, url))
    #             semantic_ans.append((False, 'RE'))
    #         else:
    #             logging.info('run semantic check success (request {}, url {})'.format(user_id, url))
    #             semantic_ans.append((result[1] == 'AC', result[1]))
    
    # # codegen_testcases = open(codegen_check_lib, 'r').readlines()
    # # codegen_ans = []
    # # ravel_user_testspace = os.path.join(ravel_bin_root, user_id)
    # # if not os.path.exists(ravel_user_testspace):
    # #     os.mkdir(ravel_user_testspace)
    # # builtin_path = os.path.join(os.path.join(compile_path, 'compiler'), 'builtin.s')
    # # if os.path.exists(builtin_path):
    # #     shutil.copy(builtin_path, os.path.join(ravel_user_testspace, 'builtin.s'))

    # # for testcase in codegen_testcases:
    # #     test_path = os.path.join(codegen_check_root, testcase)

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

def process():
    stuid, image_id, repo_url = get_stuid_image_repo(r)
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
    result = run_semantic(mysql_connection, attempt_id, uid, repo_url, image_id, submit_timestamp, compile_id)


if __name__ == '__main__':
    while True:
        process()
        time.sleep(1)