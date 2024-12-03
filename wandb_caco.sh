#!/bin/bash -l
# Standard output and error:
#SBATCH --chdir=/home/shanavas              # Set the working directory
#SBATCH --output=/home/%u/%x-%j.out           # Standard output log
#SBATCH --error=/home/%u/%x-%j.err            # Standard error log
#SBATCH --mem-per-cpu=2G                       # Memory per CPU
#SBATCH -G 1                                  # Request 1 GPU if needed, change as necessary
#SBATCH --time=0-12:00:00                      # Maximum runtime (2 hours)


module purge
#module load cuda/12.0

source ~/miniforge3/etc/profile.d/conda.sh
conda activate dfpl_env
# Run the program:
#srun wandb agent Pycharm/deepFPlearn/81yqknwm &> dfpl_run.log

srun wandb agent --count 50 dilshana-ufz/deepFPlearn-sweep/fg5s0j0a &> caco_run.log
