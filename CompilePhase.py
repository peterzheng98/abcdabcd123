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
import mysql.connector

from SetupConstant import *
from MySQLHelper import *
from DockerHelper import *

def run_compile(connection, attempt_id, user_id, url, docker_id, submit_timestamp):
    # read available image from file: docker_image_list
    with open('/srv/samba/backup/compiler-judge/docker_image_list', 'r') as f:
        image_list = f.read().split('\n')
        image_list_new = []
        for i in image_list:
            if len(i) != 0:
                image_list_new.append(i)
        image_list = image_list_new
    # check if the image is available
    if docker_id not in image_list:
        logging.error('docker image {} is not available (request {}, url {})'.format(docker_id, user_id, url))
        update_submit_status(connection, attempt_id, 'status', 3)
        return (False, None)
    
    update_submit_status(connection, attempt_id, 'status', 2)
    # generate a new uuid for this compile
    compile_id = str(uuid.uuid4())
    # generate a new path for this compile and write to log
    compile_path = os.path.join(repo_build_root_path, compile_id)
    # create corresponding folder with exception check
    try:
        os.makedirs(compile_path)
    except Exception as e:
        logging.error('create compile path failed: {} (request {}, url {})'.format(compile_path, user_id, url))
        update_submit_status(connection, attempt_id, 'status', 3)
        return (False, compile_id)
    logging.info('local compile path: {} (request {}, url {})'.format(compile_path, user_id, url))
    # change dir to compile_path
    os.chdir(compile_path)
    # clone the repo with popen, limit time
    p = subprocess.Popen(
        ['git', 'clone', url, 'compiler'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True
    )
    gitTimeout = False
    try:
        p.wait(30)
        if p.returncode != 0:
            logging.error('clone repo failed (request {}, url {})'.format(user_id, url))
            update_submit_status(connection, attempt_id, 'status', 3)
            return (False, compile_id)
    except subprocess.TimeoutExpired:
        gitTimeout = True
        if p.is_alive():
            p.terminate()
            p.wait()
    if gitTimeout:
        logging.error('clone repo timeout (request {}, url {})'.format(user_id, url))
        update_submit_status(connection, attempt_id, 'status', 3)
        update_submit_status(connection, attempt_id, 'git_message', 'Timeout')
        return (False, compile_id)
    
    # check if the repo is cloned
    if not os.path.exists(os.path.join(compile_path, 'compiler')):
        logging.error('clone repo failed (request {}, url {})'.format(user_id, url))
        update_submit_status(connection, attempt_id, 'status', 3)
        return (False, compile_id)
    # run git log to get git hash and commit message with date, using subprocess
    process = subprocess.Popen(
        ['git', 'log', '-1', '--pretty=format:%H|%s'],
        cwd=os.path.join(compile_path, 'compiler'),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        logging.error('run git log failed (request {}, url {})'.format(user_id, url))
        update_submit_status(connection, attempt_id, 'status', 3)
        return (False, compile_id)
    git_hash, git_message = stdout.split('|')
    update_submit_status(connection, attempt_id, 'githash', git_hash)
    update_submit_status(connection, attempt_id, 'git_message', git_message)

    # zip the repo and rename to {user_id}+timestamp
    repo_archive_name = '{}-{}.zip'.format(user_id, str(int(time.time())))
    # zip files to archive names
    os.system('zip -r {} {}'.format(os.path.join(repo_archive_path, repo_archive_name), compile_path))
    
    # write Dockerfile to compile_path
    docker_string = dockerfile_template.join(docker_id)
    with open(os.path.join(compile_path, 'Dockerfile'), 'w') as f:
        f.write(docker_string)
    
    # enter the compile_path, build a docker container and run it
    os.chdir(compile_path)
    # use multiprocess to build image and limit execution time to 30
    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=build_image_worker, args=(compile_path, user_id + ':latest', True, {'memory': '1024m'}, 'none', q, user_id, url))
    p.start()
    p.join(30)
    if p.is_alive():
        p.terminate()
        p.join()
        logging.error('build docker image timeout (request {}, url {})'.format(user_id, url))
        update_submit_status(connection, attempt_id, 'status', 3)
        return (False, compile_id)
    else:
        result = q.get()
        if result[0] != 0:
            logging.error('build docker image failed (request {}, url {})'.format(user_id, url))
            update_submit_status(connection, attempt_id, 'status', 3)
            return (False, compile_id)
        else:
            logging.info('build docker image success (request {}, url {})'.format(user_id, url))
            update_submit_status(connection, attempt_id, 'status', 4)
            return (True, compile_id)
