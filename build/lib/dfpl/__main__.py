import os
from argparse import Namespace
import logging
import pathlib
import dataclasses
from os import path



from tensorflow import keras
import wandb

from dfpl.utils import makePathAbsolute, createDirectory
from dfpl import options
from dfpl import fingerprint as fp
from dfpl import autoencoder as ac
from dfpl import feedforwardNN as fNN
from dfpl import predictions
from dfpl import single_label_model as sl
from dfpl import normalization
from sklearn.preprocessing import MinMaxScaler
import pandas as pd
import pickle
import jsonpickle
from pathlib import Path
import sys
sys.path.append("/home/shanavas/PycharmProjects/deepFPlearn/dfpl/")
from normalization import normalize_acc_values, inverse_transform_predictions

wandb.init()

project_directory = pathlib.Path(".").parent.parent.absolute()
test_train_opts = options.Options(
    inputFile=f'{project_directory}/input_datasets/toxcast_regression_AR.csv',
    outputDir=f'{project_directory}/output_data/console_test',
    ecWeightsFile=f'{project_directory}/output_data/case_regression_01/AR/ae.encoder.hdf5',
    ecModelDir=f'{project_directory}/output_data/case_regression_01/AR/saved_model',
    type='inchi',
    fpType='topological',
    epochs=100,
    batchSize=1024,
    fpSize=2048,
    encFPSize=256,
    enableMultiLabel=False,
    testSize=0.2,
    kFolds=1,
    verbose=2,
    trainAC=False,
    trainFNN=True,
    compressFeatures=False,
    activationFunction="selu",
    lossFunction='mae',
    optimizer='Adam',
    fnnType='REG',  # todo: replace useRegressionModel with fnnType variable
    wabTarget='AR',
    wabTracking=True,
    normalizeACC = False #dilshana
)

test_pred_opts = options.Options(
    inputFile=f"{project_directory}/input_datasets/S_dataset.pkl",
    outputDir=f"{project_directory}/output_data/console_test",
    outputFile=f"{project_directory}/output_data/console_test/S_dataset.predictions_ER.csv",
    ecModelDir=f"{project_directory}/output_data/case_00/AE_S/saved_model",
    fnnModelDir=f"{project_directory}/output_data/console_test/ER_saved_model",
    type="smiles",
    fpType="topological"
)


def train(opts: options.Options):
    """
    Run the main training procedure
    :param opts: Options defining the details of the training
    """

    if opts.wabTracking:
        wandb.init(project=f"dfpl-reg-training-{opts.wabTarget}", entity="dfpl_regression", config=vars(opts))
        # opts = wandb.config

    # df = fp.importDataFile(opts.inputFile, import_function=fp.importSmilesCSV, fp_size=opts.fpSize)
    df = fp.importDataFile(opts.inputFile, import_function=fp.importCSV, fp_size=opts.fpSize)

    # Create output dir if it doesn't exist
    createDirectory(opts.outputDir)  # why? we just created that directory in the function before??
    encoder = None





    if opts.trainAC:
        # train an autoencoder on the full feature matrix
        encoder = ac.train_full_ac(df, opts)

    if opts.compressFeatures:

        if not opts.trainAC:
            # load trained model for autoencoder
            encoder = keras.models.load_model(opts.ecModelDir)

        # compress the fingerprints using the autoencoder
        df = ac.compress_fingerprints(df, encoder)

    if opts.normalizeACC:   #dilshana
        df, scaler_path = normalize_acc_values(df, column_name='AR', output_dir=opts.outputDir)

    if opts.trainFNN:
        # train single label models
        # fNN.train_single_label_models(df=df, opts=opts)
        sl.train_single_label_models(df=df, opts=opts)

    # train multi-label models
    if opts.enableMultiLabel:
        fNN.train_nn_models_multi(df=df, opts=opts)


#dilshana

def predict(opts: options.Options) -> None:
    """
    Run prediction given specific options
    :param opts: Options defining the details of the prediction
    """
    df = fp.importDataFile(opts.inputFile, import_function=fp.importSmilesCSV, fp_size=opts.fpSize)


    # Create output dir if it doesn't exist
    createDirectory(opts.outputDir)

    if opts.compressFeatures:
        # load trained model for autoencoder
        encoder = keras.models.load_model(opts.ecModelDir)
       # compress the fingerprints using the autoencoder
        df = ac.compress_fingerprints(df, encoder)

    logging.info(f"Raw predictions: {df.head()}")
    # predict
    df2 = predictions.predict_values(df=df, opts=opts)
    print(df2.head())
    logging.info(f"Raw predictions: {df2['predicted'].head()}")


    if opts.scalerFilePath:


        df2['predicted'] = inverse_transform_predictions(df2['predicted'].values, opts.scalerFilePath)

        #normalized_file = os.path.join(opts.outputDir, "normalized_predictions.csv")
        #logging.info(f"Saving normalized predictions to {normalized_file}")
        #df2.to_csv(path_or_buf=normalized_file, index=False)

    else:
        logging.warning("Normalization is enabled but scalerFilePath is not provided in the options. Skipping normalization step.")


    names_columns = [c for c in df2.columns if c not in ['fp', 'fpcompressed']]

    df2[names_columns].to_csv(path_or_buf=path.join(opts.outputDir, opts.outputFile))
    logging.info(f"Prediction successful. Results written to '{path.join(opts.outputDir, opts.outputFile)}'")






def createLogger(filename: str) -> None:
    """
    Set up a logger for the main function that also saves to a log file
    """
    # get root logger and set its level
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    # create file handler which logs info messages
    fh = logging.FileHandler(filename, mode="w")
    fh.setLevel(logging.INFO)
    # create console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    # create formatter and add it to the handlers
    formatterFile = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    formatterConsole = logging.Formatter('%(levelname)-8s %(message)s')
    fh.setFormatter(formatterFile)
    ch.setFormatter(formatterConsole)
    # add the handlers to the logger
    logger.addHandler(fh)
    logger.addHandler(ch)


def main():
    """
    Main function that runs training/prediction defined by command line arguments
    """
    parser = options.createCommandlineParser()
    prog_args: Namespace = parser.parse_args()

    try:
        if prog_args.method == "convert":
            directory = makePathAbsolute(prog_args.f)
            if path.isdir(directory):
                createLogger(path.join(directory, "convert.log"))
                logging.info(f"Convert all data files in {directory}")
                fp.convert_all(directory)
            else:
                raise ValueError("Input directory is not a directory")
        if prog_args.method == "train":
            train_opts = options.Options.fromCmdArgs(prog_args)
            fixed_opts = dataclasses.replace(
                train_opts,
                inputFile=makePathAbsolute(train_opts.inputFile),
                outputDir=makePathAbsolute(train_opts.outputDir)
            )
            createDirectory(fixed_opts.outputDir)
            createLogger(path.join(fixed_opts.outputDir, "train.log"))
            logging.info(f"The following arguments are received or filled with default values:\n{fixed_opts}")
            train(fixed_opts)
            exit(0)
        elif prog_args.method == "predict":
            predict_opts = options.Options.fromCmdArgs(prog_args)
            fixed_opts = dataclasses.replace(
                predict_opts,
                inputFile=makePathAbsolute(predict_opts.inputFile),
                outputDir=makePathAbsolute(predict_opts.outputDir),
                outputFile=makePathAbsolute(path.join(predict_opts.outputDir, predict_opts.outputFile)),
                ecModelDir=makePathAbsolute(predict_opts.ecModelDir),
                fnnModelDir=makePathAbsolute(predict_opts.fnnModelDir),
                trainAC=False,
                trainFNN=False
            )
            createDirectory(fixed_opts.outputDir)
            createLogger(path.join(fixed_opts.outputDir, "predict.log"))
            logging.info(f"The following arguments are received or filled with default values:\n{prog_args}")
            predict(fixed_opts)
            exit(0)
    except AttributeError as e:
        print(e)
        parser.print_usage()


if __name__ == '__main__':
    main()