import docker
import os
import uuid
import logging
import sys
import time
import queue
import multiprocessing


# base path
repo_archive_path = '/srv/samba/backup/compiler-judge/archive'
repo_build_root_path = '/srv/samba/backup/compiler-judge/temp'
log_path = '/srv/samba/backup/compiler-judge/log/{}'.format(sys.argv[1])

# dockerfile template: from {} but copy all files in the repo to /compiler and then build and tag
dockerfile_template = '''FROM {}

COPY . /compiler
RUN cd /compiler && make build'''


# set logging with date time, and both write to files
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    filename=log_path,
                    filemode='a')


def build_image_worker(path, tag, rm, container_limits, network_mode, q, user_id, url):
    client = docker.from_env()
    try:
        user_image = client.images.build(path=path, tag=tag, rm=rm, container_limits=container_limits, network_mode=network_mode)
        message = ''
        for chunk in user_image[1]:
            if 'stream' in chunk:
                message = message + chunk['stream'].strip() + '\n'
            elif 'errorDetail' in chunk:
                message = message + chunk['errorDetail']['message'].strip() + '\n'
        q.put((0, message))
        logging.info('[worker]build docker image success {} (request {}, url {})'.format(user_image[0].id, user_id, url))
    except docker.errors.BuildError as e:
        err_message = ''
        for line in e.build_log:
            if 'stream' in line:
                err_message += (line['stream'].strip() + '\n')
            elif 'errorDetail' in line:
                err_message += (line['errorDetail']['message'].strip() + '\n')
        q.put((1, err_message))
        logging.error('[worker]build docker image failed {} (request {}, url {})'.format(e, user_id, url))
    except Exception as e:
        logging.error('[worker]build docker image general failed {} (request {}, url {})'.format(e, user_id, url))
        q.put((2, ''))


def run_compile(user_id, url, docker_id):
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
        logging.error('docker image {} is not available (request {}, url {})'.format(docker_id))
        return False
    
    # generate a new uuid for this compile
    compile_id = str(uuid.uuid4())
    # generate a new path for this compile and write to log
    compile_path = os.path.join(repo_build_root_path, compile_id)
    # create corresponding folder with exception check
    try:
        os.makedirs(compile_path)
    except Exception as e:
        logging.error('create compile path failed: {} (request {}, url {})'.format(compile_path, user_id, url))
        return False
    logging.info('local compile path: {} (request {}, url {})'.format(compile_path, user_id, url))
    # change dir to compile_path
    os.chdir(compile_path)
    # clone the repo
    os.system('git clone {} compiler'.format(url))
    # check if the repo is cloned
    if not os.path.exists(os.path.join(compile_path, 'compiler')):
        logging.error('clone repo failed (request {}, url {})'.format(user_id, url))
        return False
    
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
        return False
    else:
        result = q.get()
        if result[0] != 0:
            logging.error('build docker image failed (request {}, url {})'.format(user_id, url))
            return False
        else:
            logging.info('build docker image success (request {}, url {})'.format(user_id, url))
            return True
    
    
    
    