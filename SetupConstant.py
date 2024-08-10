import sys

# base path
GlobalName = 'abcd'
repo_archive_path = '/srv/samba/backup/compiler-judge/archive/{}'.format(sys.argv[1])
repo_build_root_path = '/srv/samba/backup/compiler-judge/temp/{}'.format(sys.argv[1])
log_path = '/srv/samba/backup/compiler-judge/log/{}.log'.format(sys.argv[1])
output_logs = '/srv/samba/backup/compiler-judge/output-logs/{}'.format(sys.argv[1])

# dockerfile template: from {} but copy all files in the repo to /compiler and then build and tag
dockerfile_template = '''FROM {}

COPY . /compiler
RUN cd /compiler && make build
ENTRYPOINNT ["make", "run"]
'''

# for semantic
semantic_check_root = '' # the root path for semantic check
semantic_check_lib  = '.txt' # the lib for semantic check

# for codegen
ravel_bin_root = ''
codegen_check_root = '' # the root path for semantic check
codegen_check_lib  = '.txt' # the lib for semantic check

BASE_HOST = "10.5.5.106"
