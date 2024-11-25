import logging
import math
import shutil
import sys
import os
from os import path
from time import time

import numpy as np
import pandas as pd
from keras.metrics import RootMeanSquaredError
from sklearn.metrics import auc
from sklearn.metrics import classification_report
from sklearn.metrics import confusion_matrix
from sklearn.metrics import matthews_corrcoef
from sklearn.metrics import roc_curve
from sklearn.model_selection import StratifiedKFold, KFold
from sklearn.model_selection import train_test_split
import tensorflow as tf
from tensorflow.keras import metrics
from tensorflow.keras import losses
from tensorflow.keras import optimizers
from tensorflow.keras import regularizers
from tensorflow.keras.layers import Dense, Dropout, AlphaDropout
from tensorflow.keras.models import Model
from tensorflow.keras.models import Sequential
import tensorflow.keras.backend as K

from dfpl import callbacks as cb
from dfpl import options
from dfpl import plot as pl
from dfpl import settings


def sample_down_data(opts: options.Options, df: pd.DataFrame, target: str, column: str) -> (np.ndarray,
                                                                                            np.ndarray):
    assert 0.0 < opts.sampleFractionOnes < 1.0
    logging.info(f"Using fractional sampling {opts.sampleFractionOnes}")
    # how many ones
    counts = df[target].value_counts()
    logging.info(f"Number of sampling values: {counts.to_dict()}")
    # add sample of 0s to df of 1s
    df_x = pd.concat([
        df[df[target] == 1],
        df[df[target] == 0].sample(int(min(counts[0], counts[1] / opts.sampleFractionOnes)))
    ])
    x = np.array(
        df_x[column].to_list(),
        dtype=settings.nn_fp_compressed_numpy_type if opts.compressFeatures else settings.nn_fp_numpy_type,
        copy=settings.numpy_copy_values

    )
    y = np.array(
        df_x[target].to_list(),
        dtype=settings.nn_target_numpy_type,
        copy=settings.numpy_copy_values
    )

    return x, y


def prepare_nn_training_data(df: pd.DataFrame, target: str, opts: options.Options) -> (np.ndarray,
                                                                                       np.ndarray):
    # default minimal imbalance that is accepted
    allowed_min_imbalance = 0.1

    fp_col = "fpcompressed" if opts.compressFeatures else "fp"
    df_train = df[df[target].notna() & df[fp_col].notnull()][[target, fp_col]]

    if opts.fnnType == "REG":
        # extract X and y
        x = np.array(
            df_train[fp_col].to_list(),
            dtype=settings.nn_fp_compressed_numpy_type if opts.compressFeatures else settings.nn_fp_numpy_type,
            copy=settings.numpy_copy_values
        )
        y = np.array(
            df_train[target].to_list(),
            dtype=settings.nn_target_numpy_type_regression,
            copy=settings.numpy_copy_values
        )
    else:
        # check for imbalance
        vc = df_train[target].value_counts()
        if min(vc) < max(vc) * allowed_min_imbalance:
            logging.info(
                f" Your training data is extremely unbalanced ({target}): 0 - {vc[0]}, and 1 - {vc[1]} values.")
            if opts.sampleDown:
                logging.info(f" I will down-sample your data")
                if not opts.sampleFractionOnes:
                    opts.sampleFractionOnes = allowed_min_imbalance
                return sample_down_data(opts=opts, target=target, df=df_train, column=fp_col)
            else:
                logging.info(f" I will not down-sample your data automatically.")
                logging.info(f" Consider to enable down sampling of the 0 values with --sampleDown option.")

        x = np.array(
            df_train[fp_col].to_list(),
            dtype=settings.nn_fp_compressed_numpy_type if opts.compressFeatures else settings.nn_fp_numpy_type,
            copy=settings.numpy_copy_values
        )
        y = np.array(
            df_train[target].to_list(),
            dtype=settings.nn_target_numpy_type,
            copy=settings.numpy_copy_values
        )

    return x, y


def build_fnn_network(input_size: int, opts: options.Options, output_bias=None) -> Model:
    if output_bias is not None:
        output_bias = tf.keras.initializers.Constant(output_bias)
    my_hidden_layers = {"2048": 6, "1024": 5, "999": 5, "512": 4, "256": 3}
    if not str(input_size) in my_hidden_layers.keys():
        raise ValueError("Wrong input-size. Must be in {2048, 1024, 999, 512, 256}.")

    nhl = int(math.log2(input_size) / 2 - 1)

    model = Sequential()
    # From input to 1st hidden layer
    if opts.activationFunction == "relu":
        model.add(Dense(units=int(input_size / 2),
                        input_dim=input_size,
                        activation="relu",
                        kernel_regularizer=regularizers.l2(opts.l2reg),
                        kernel_initializer="he_uniform"))
        model.add(Dropout(opts.dropout))
    elif opts.activationFunction == "selu":
        model.add(Dense(units=int(input_size / 2),
                        input_dim=input_size,
                        activation="selu",
                        kernel_initializer="lecun_normal"))
        model.add(AlphaDropout(opts.dropout))
    else:
        logging.error("Only 'relu' and 'selu' activation is supported")
        sys.exit(-1)

    # next hidden layers
    for i in range(1, nhl):
        factor_units = 2 ** (i + 1)
        factor_dropout = 2 * i
        if opts.activationFunction == "relu":
            model.add(Dense(units=int(input_size / factor_units),
                            activation="relu",
                            kernel_regularizer=regularizers.l2(opts.l2reg),
                            kernel_initializer="he_uniform"))
            model.add(Dropout(opts.dropout / factor_dropout))
        elif opts.activationFunction == "selu":
            model.add(Dense(units=int(input_size / factor_units),
                            activation="selu",
                            kernel_initializer="lecun_normal"))
            model.add(AlphaDropout(opts.dropout / factor_dropout))
        else:
            logging.error("Only 'relu' and 'selu' activation is supported")
            sys.exit(-1)

    model.add(Dense(units=1, activation='sigmoid', bias_initializer=output_bias))
    return model


def build_snn_network(input_size: int, opts: options.Options, output_bias=None) -> Model:
    if output_bias is not None:
        output_bias = tf.keras.initializers.Constant(output_bias)
    model = Sequential()
    model.add(Dense(input_dim=input_size, units=50, activation="selu", kernel_initializer="lecun_normal"))
    model.add(AlphaDropout(opts.dropout))

    for i in range(7):
        model.add(Dense(units=50, activation="selu", kernel_initializer="lecun_normal"))
        model.add(AlphaDropout(opts.dropout))
    model.add(Dense(units=1, activation="sigmoid", bias_initializer=output_bias))
    return model


def build_simple_regression_network(input_size: int, opts: options.Options, output_bias=None) -> Model:
    if output_bias is not None:
        output_bias = tf.keras.initializers.Constant(output_bias)

    model = Sequential([
        Dense(round(input_size / 2, ndigits=0), input_dim=input_size, activation=opts.activationFunction,
              kernel_regularizer=regularizers.l2(opts.l2reg),
              kernel_initializer="he_uniform"),  # Start with a moderate size
        Dropout(opts.dropout),  # Add dropout for regularization
        Dense(round(input_size / 4, ndigits=0), activation=opts.activationFunction,
              kernel_regularizer=regularizers.l2(opts.l2reg),
              kernel_initializer="he_uniform"),  # Reduce the size
        Dropout(opts.dropout),  # Add dropout
        Dense(round(input_size / 8, ndigits=0), activation=opts.activationFunction,
              kernel_regularizer=regularizers.l2(opts.l2reg),
              kernel_initializer="he_uniform"),  # Further reduce the size
        Dense(1, activation='linear', bias_initializer=output_bias)  # Output layer for regression
    ])

    return model


def build_regression_network(input_size: int, opts: options.Options, output_bias=None) -> Model:
    if output_bias is not None:
        output_bias = tf.keras.initializers.Constant(output_bias)

    nhl = int(math.log2(input_size) / 2 - 1)

    model = Sequential()
    # From input to 1st hidden layer
    if opts.activationFunction == "relu":
        model.add(Dense(units=int(input_size / 2),
                        input_dim=input_size,
                        activation="relu",
                        kernel_regularizer=regularizers.l2(opts.l2reg),
                        kernel_initializer="he_uniform"))
        model.add(Dropout(opts.dropout))
    elif opts.activationFunction == "selu":
        model.add(Dense(units=int(input_size / 2),
                        input_dim=input_size,
                        activation="selu",
                        kernel_initializer="lecun_normal"))
        model.add(AlphaDropout(opts.dropout))
    else:
        logging.error("Only 'relu' and 'selu' activation is supported")
        sys.exit(-1)

    # next hidden layers
    for i in range(1, nhl):
        factor_units = 2 ** (i + 1)
        factor_dropout = 2 * i
        if opts.activationFunction == "relu":
            model.add(Dense(units=int(input_size / factor_units),
                            activation="relu",
                            kernel_regularizer=regularizers.l2(opts.l2reg),
                            kernel_initializer="he_uniform"))
            model.add(Dropout(opts.dropout / factor_dropout))
        elif opts.activationFunction == "selu":
            model.add(Dense(units=int(input_size / factor_units),
                            activation="selu",
                            kernel_initializer="lecun_normal"))
            model.add(AlphaDropout(opts.dropout / factor_dropout))
        else:
            logging.error("Only 'relu' and 'selu' activation is supported")
            sys.exit(-1)

    model.add(Dense(units=1,
                    # activation='sigmoid',
                    bias_initializer=output_bias))
    return model



def define_single_label_model(input_size: int, opts: options.Options, output_bias=None) -> Model:
    if opts.lossFunction == "bce":
        loss_function = losses.BinaryCrossentropy()
    elif opts.lossFunction == "mse":
        loss_function = losses.MeanSquaredError()
    elif opts.lossFunction == 'mae':
        loss_function = losses.MeanAbsoluteError()

    else:
        logging.error(f"Your selected loss is not supported: {opts.lossFunction}.")
        sys.exit("Unsupported loss function")

    if opts.optimizer == 'Adam':
        my_optimizer = optimizers.Adam(learning_rate=opts.learningRate)
    elif opts.optimizer == 'SGD':
        my_optimizer = optimizers.SGD(lr=opts.learningRate, momentum=0.9)
    else:
        logging.error(f"Your selected optimizer is not supported: {opts.optimizer}.")
        sys.exit("Unsupported optimizer")

    if opts.fnnType == "FNN":
        model = build_fnn_network(input_size, opts, output_bias)
    elif opts.fnnType == "SNN":
        model = build_snn_network(input_size, opts, output_bias)
    elif opts.fnnType == "REG":
        # model = build_regression_network(input_size, opts, output_bias)
        model = build_simple_regression_network(input_size, opts, output_bias)
    else:
        raise ValueError(f"Option FNN Type is not \"FNN\", \"SNN\", or \"REG\", but {opts.fnnType}.")
    logging.info(f"Network type: {opts.fnnType}")
    model.summary(print_fn=logging.info)

    if opts.fnnType == "REG":
        model.compile(loss=loss_function,
                      optimizer=my_optimizer,
                      metrics=[
                          metrics.RootMeanSquaredError(name="rmse"),
                          metrics.MeanSquaredError(name="mse"),
                          metrics.MeanAbsoluteError(name="mae")
                      ]
                      )
    else:
        model.compile(loss=loss_function,
                      optimizer=my_optimizer,
                      metrics=[metrics.BinaryAccuracy(name="accuracy"),
                               metrics.AUC(),
                               metrics.Precision(),
                               metrics.Recall()]
                      )
    return model


def acper(y_true, y_pred, t: float = 0.02):
    """
    This function calculates Almost Correct Predictions Error Rate (ACPER)
    :param t: value to define 'almost' correctness
    :param y_true: list of real numbers, true values
    :param y_pred: list of real numbers, predicted values
    :returns: acper score
    """
    # threshold = 0.02
    for yt, yp in zip(y_true, y_pred):
        lower_bound = yt - (t * yt)
        upper_bound = yt + (t * yt)
        if (yp >= lower_bound) & (yp <= upper_bound):
            yield True
        else:
            yield False


def evaluate_regression_model(x_test: np.ndarray, y_test: np.ndarray,file_prefix: str, model: Model,
                              target: str, fold: int, threshold: float = 0.05) -> pd.DataFrame:
    """
    This function returns the values of performance metrics for the regression model.
    It covers Mean Squared Error (MSE), Mean Absolute Error (MAE), Median Absolute Error (MdAE),
    Almost Correct Predictions Error Rate (ACPER), Mean Absolute Percentage Error (MAPE), and
    Root Mean Squared Error (RMSE).
    See https://towardsdatascience.com/assessing-model-performance-for-regression-7568db6b2da0 for detailed
    explanations of the error metrics.

    :param threshold: The threshold value for ACPER score
    :param x_test: Holdout data
    :param y_test: Target values for holdout data
    :param file_prefix: Selected model file path prefix
    :param model: Selected model
    :param target: Target of concern
    :param fold: Fold of concern
    :return: Dataframe containing the metric values (in rows)
    """

    name = path.basename(file_prefix).replace("_", " ")
    logging.info(f"Evaluating trained model '{name}' on test data")

    y_predict = model.predict(x_test).flatten()
    pd.DataFrame(y_predict).to_csv(path_or_buf=f"{file_prefix}.y_test_predict.csv")
    error = np.array(y_predict) - np.array(y_test)
    abs_error = abs(error)

     # Compute R² (coefficient of determination)
    ss_res = np.sum((y_test - y_predict) ** 2)  # Residual sum of squares
    ss_tot = np.sum((y_test - np.mean(y_test)) ** 2)  # Total sum of squares
    r2 = 1 - (ss_res / (ss_tot + np.finfo(float).eps))  # Add small epsilon to avoid division by zero

    regression_metrics = ['MSE', 'MAE', 'MdAE', 'ACPER', 'MAPE', 'RMSE','R2']
    metric_values = [
        np.mean(abs_error ** 2, axis=0),
        np.mean(abs_error, axis=0),
        np.median(abs_error, axis=0),
        sum(list(acper(y_true=y_test, y_pred=y_predict, t=threshold))) / len(y_test),
        np.mean(abs_error / np.array(y_test), axis=0),
        np.sqrt(np.mean(abs_error ** 2, axis=0)),
        r2 #R2
    ]

    return pd.DataFrame({'metric': regression_metrics, 'value': metric_values, 'fold': fold, 'target': target})


def evaluate_model(x_test: np.ndarray, y_test: np.ndarray, file_prefix: str, model: Model,
                   target: str, fold: int, threshold: float = 0.5) -> pd.DataFrame:
    logging.info(f"Evaluating trained model '{file_prefix}' on test data")

    # predict test set to compute MCC, AUC, ROC curve, etc. (see below)
    y_predict = model.predict(x_test).flatten()
    y_predict_int = (y_predict >= threshold).astype(np.short)
    y_test_int = y_test.astype(np.short)

    (pd
     .DataFrame({
        "y_true": y_test_int,
        "y_predicted": y_predict,
        "y_predicted_int": y_predict_int,
        "target": target,
        "fold": fold})
     .to_csv(path_or_buf=f"{file_prefix}.predicted.testdata.csv")
     )

    # compute the metrics that depend on the class label
    precision_recall = classification_report(y_test_int, y_predict_int, output_dict=True)
    prf = pd.DataFrame.from_dict(precision_recall)[['0', '1']]
    prf.to_csv(path_or_buf=f"{file_prefix}.predicted.testdata.prec_rec_f1.csv")

    # validation loss, accuracy, auc, precision, recall
    loss, acc, auc_value, precision, recall = tuple(model.evaluate(x=x_test, y=y_test))
    logging.info(f"Loss: {round(loss, 4)}")
    logging.info(f"Accuracy: {round(acc, 4)}")
    logging.info(f"AUC: {round(auc_value, 4)}")
    logging.info(f"Precision: {round(precision, 4)}")
    logging.info(f"Recall: {round(recall, 4)}")

    mcc = matthews_corrcoef(y_test_int, y_predict_int)
    logging.info(f"MCC: {round(mcc, 4)}")

    # generate the AUC-ROC curve data from the validation data
    fpr, tpr, thresholds_keras = roc_curve(y_true=y_test_int,
                                           y_score=y_predict,
                                           drop_intermediate=False)
    auc_val = auc(fpr, tpr)
    # save AUC data to csv
    pd.DataFrame(list(zip(fpr, tpr, [auc_val] * len(fpr), [target] * len(fpr), [fold] * len(fpr))),
                 columns=['fpr', 'tpr', 'auc_value', 'target', 'fold']). \
        to_csv(path_or_buf=f"{file_prefix}.predicted.testdata.aucdata.csv")

    pl.plot_auc(fpr=fpr, tpr=tpr, target=target, auc_value=auc_val,
                filename=f"{file_prefix}.predicted.testdata.aucdata.svg")

    # [[tn, fp]
    #  [fn, tp]]
    cfm = confusion_matrix(y_true=y_test_int,
                           y_pred=y_predict_int)

    logging.info(f"TP/FP: {cfm[1][1]}/{cfm[0][1]}")
    logging.info(f"TN/FN: {cfm[0][0]}/{cfm[1][0]}")

    classification_metrics = ['Precision_0', 'Recall_0', 'F1_0',
                              'Precision_1', 'Recall_1', 'F1_1',
                              'Loss', 'Accuracy', 'MCC', 'AUC',
                              'TN', 'FN', 'FP', 'TP']
    metric_values = [prf['0']['precision'], prf['0']['recall'], prf['0']['f1-score'],
                     prf['1']['precision'], prf['1']['recall'], prf['1']['f1-score'],
                     loss, acc, mcc, auc_val,
                     cfm[0][0], cfm[1][0], cfm[0][1], cfm[1][1]
                     ]

    return pd.DataFrame({'metric': classification_metrics, 'value': metric_values, 'fold': fold, 'target': target})


def fit_and_evaluate_model(x_train: np.ndarray, x_test: np.ndarray, y_train: np.ndarray, y_test: np.ndarray,
                           fold: int, target: str, opts: options.Options) -> pd.DataFrame:
    logging.info(f"Training of fold number: {fold}")
    logging.info(f"Training sample distribution: train data: {pd.DataFrame(y_train)[0].value_counts().to_dict()} "
                 f"test data: {pd.DataFrame(y_test)[0].value_counts().to_dict()}")

    model_file_prefix = path.join(opts.outputDir, f"{target}_single-labeled_Fold-{fold}")

    if opts.fnnType == 'REG':
        initial_bias = None
    else:
        ids, counts = np.unique(y_train, return_counts=True)
        count_dict = dict(zip(ids, counts))
        if count_dict[0] == 0:
            logging.info("No zeroes in training labels. Setting initial_bias to None.")
            initial_bias = None
        else:
            initial_bias = np.log([count_dict[1] / count_dict[0]])
            logging.info(f"Initial bias for last sigmoid layer: {initial_bias[0]}")

    model = define_single_label_model(input_size=x_train.shape[1], opts=opts, output_bias=initial_bias)

    checkpoint_model_weights_path = f"{model_file_prefix}.model.weights.hdf5"
    callback_list = cb.nn_callback(checkpoint_path=checkpoint_model_weights_path,
                                   opts=opts)

    # measure the training time
    start = time()
    hist = model.fit(x_train, y_train,
                     callbacks=callback_list,
                     epochs=opts.epochs,
                     batch_size=opts.batchSize,
                     verbose=opts.verbose,
                     validation_data=(x_test, y_test)
                     )
    train_time = str(round((time() - start) / 60, ndigits=2))
    logging.info(f"Computation time for training the single-label model for {target}: {train_time} min")

    # save and plot history
    pd.DataFrame(hist.history).to_csv(path_or_buf=f"{model_file_prefix}.history.csv")
    pd.DataFrame(x_train).to_csv(path_or_buf=f"{model_file_prefix}.x_train.csv")
    pd.DataFrame(y_train).to_csv(path_or_buf=f"{model_file_prefix}.y_train.csv")
    pd.DataFrame(x_test).to_csv(path_or_buf=f"{model_file_prefix}.x_test.csv")
    pd.DataFrame(y_test).to_csv(path_or_buf=f"{model_file_prefix}.y_test.csv")

    # use callback model for evaluation
    callback_model = define_single_label_model(input_size=x_train.shape[1], opts=opts)
    callback_model.load_weights(filepath=checkpoint_model_weights_path)
   # save_split_data(x_train, x_test, y_train, y_test, fold=fold, target=target,opts=opts)

    if opts.fnnType == 'REG':

        # Predict train and test
        y_train_pred = callback_model.predict(x_train).flatten()
        y_test_pred = callback_model.predict(x_test).flatten()


        pl.plot_loss(hist=hist, file=f"{model_file_prefix}.history.jpg")
        performance = evaluate_regression_model(x_test=x_test, y_test=y_test, file_prefix=model_file_prefix,
                                                model=callback_model,
                                                target=target, fold=fold)

                # RMSE calculations
        rmse_train = np.sqrt(np.mean((y_train - y_train_pred) ** 2))
        rmse_test = np.sqrt(np.mean((y_test - y_test_pred) ** 2))

        # R² calculations
        ss_res_train = np.sum((y_train - y_train_pred) ** 2)
        ss_tot_train = np.sum((y_train - np.mean(y_train)) ** 2)
        r2_train = 1 - (ss_res_train / (ss_tot_train + np.finfo(float).eps))

        ss_res_test = np.sum((y_test - y_test_pred) ** 2)
        ss_tot_test = np.sum((y_test - np.mean(y_test)) ** 2)
        r2_test = 1 - (ss_res_test / (ss_tot_test + np.finfo(float).eps))

        # Save metrics to a separate file
        metrics_file = f"{model_file_prefix}.metrics.csv"
        metrics_df = pd.DataFrame({
            "Dataset": ["Train", "Test"],
            "RMSE": [rmse_train, rmse_test],
            "R²": [r2_train, r2_test]
        })
        metrics_df.to_csv(metrics_file, index=False)
        logging.info(f"Metrics saved to {metrics_file}")


        # Plot predictions vs. actual for both train and test
        y_train_pred = callback_model.predict(x_train).flatten()
        y_test_pred = callback_model.predict(x_test).flatten()
        pl.plot_predictions_vs_actual(y_train, y_train_pred, y_test, y_test_pred,
                                       file=f"{model_file_prefix}.predicted_vs_actual.png")


    else:
        pl.plot_history(history=hist, file=f"{model_file_prefix}.history.svg")
        performance = evaluate_model(x_test=x_test, y_test=y_test, file_prefix=model_file_prefix, model=callback_model,
                                     target=target, fold=fold)

    return performance
#def save_split_data(x_train, x_test, y_train, y_test, fold, target,opts: options.Options):
#    """Helper function to save combined x and y data in the same CSV files for train/test splits."""
#        # Combine x and y into a single DataFrame for train and test
#    train_df = pd.DataFrame(x_train)
#    train_df[target] = y_train  # Adding y values as a new column to the x data

#    test_df = pd.DataFrame(x_test)
#    test_df[target] = y_test  # Adding y values as a new column to the x data

        # Generate file names based on fold_no (0 for single fold) and save CSVs
#    train_df.to_csv(os.path.join(opts.outputDir, f"train_fold_{fold}_{target}.csv"), index=True)
#    test_df.to_csv(os.path.join(opts.outputDir, f"test_fold_{fold}_{target}.csv"), index=True)




def train_single_label_models(df: pd.DataFrame, opts: options.Options) -> None:
    """
    Train individual models for all targets (columns) present in the provided target data (y) and a multi-label
    model that classifies all targets at once. For each individual target the data is first subset to exclude NA
    values (for target associations). A random sample of the remaining data (size is the split fraction) is used for
    training and the remaining data for validation.

    :param opts: The command line arguments in the options class
    :param df: The dataframe containing x matrix and at least one column for a y target.
    """

    # find target columns
    names_y = [c for c in df.columns if c not in ['cid', 'ID', 'id', 'mol_id', 'smiles', 'fp', 'inchi', 'fpcompressed']]

    if opts.wabTracking:
        # For W&B tracking, we only train one target that's specified as wabTarget "ER".
        # In case it's not there, we use the first one available
        if opts.wabTarget in names_y:
            names_y = [opts.wabTarget]
        else:
            logging.error(f"The specified wabTarget for Weights & Biases tracking does not exist: {opts.wabTarget}")
            names_y = [names_y[0]]

    # Collect metrics for each fold and target
    performance_list = []

    # For each individual target train a model
    for target in names_y:  # [:1]:
        # target=names_y[0] # --> only for testing the code
        x, y = prepare_nn_training_data(df, target, opts)
        if x is None:
            continue

        logging.info(f"X training matrix of shape {x.shape} and type {x.dtype}")
        logging.info(f"Y training matrix of shape {y.shape} and type {y.dtype}")

        if opts.kFolds == 1:
            split_random_state = 1 if opts.wabTracking else None
            # for single 'folds' and when sweeping on W&B, we fix the random state
            split_stratify = None if opts.fnnType == 'REG' else y
            x_train, x_test, y_train, y_test = train_test_split(x, y,
                                                                stratify=split_stratify,
                                                                test_size=opts.testSize,
                                                                random_state=split_random_state)

            performance = fit_and_evaluate_model(x_train=x_train, x_test=x_test,
                                                 y_train=y_train, y_test=y_test,
                                                 fold=0, target=target, opts=opts)
            performance_list.append(performance)

            # save complete model
            trained_model = define_single_label_model(input_size=len(x[0]), opts=opts)
            trained_model.load_weights(path.join(opts.outputDir, f"{target}_single-labeled_Fold-0.model.weights.hdf5"))
            trained_model.save(filepath=path.join(opts.outputDir, f"{target}_saved_model"))


        elif 1 < opts.kFolds < int(x.shape[0] / 100):
            # do a k-fold cross-validation
            if opts.fnnType != 'REG':
                kfold_c_validator = StratifiedKFold(n_splits=opts.kFolds, shuffle=True, random_state=42)
            else:
                kfold_c_validator = KFold(n_splits=opts.kFolds, shuffle=True, random_state=42)



            fold_no = 1
            # split the data
            for train, test in kfold_c_validator.split(x, y):
                # for testing use one of the splits:
                # kf = kfold_c_validator.split(x, y)
                # train, test = next(kf)
                train_indices_list = pd.DataFrame(train)
                test_indices_list = pd.DataFrame(test)

                performance = fit_and_evaluate_model(x_train=x[train], x_test=x[test],
                                                     y_train=y[train], y_test=y[test],
                                                     fold=fold_no, target=target, opts=opts)
                performance_list.append(performance)

               # fold_no += 1
                # now next fold
                train_indices_list.to_csv(os.path.join(opts.outputDir, f"train_fold_{fold_no}.csv"),index=False,header=["Train Index"])
                test_indices_list.to_csv(os.path.join(opts.outputDir, f"test_fold_{fold_no}.csv"),index=False,header=["Test Index"])
                fold_no += 1


        # select and copy best model - how to define the best model?
        if opts.fnnType == 'REG':
            best_fold = (
                pd.concat(
                    performance_list, ignore_index=True).sort_values(
                    by=['value'],
                    ascending=False,
                    ignore_index=True)['fold'][0]
            )
        else:
            best_fold = (
                pd.concat(
                    performance_list, ignore_index=True).sort_values(
                    by=['p_1', 'r_1', 'MCC'],
                    ascending=False,
                    ignore_index=True)['fold'][0]
            )





        # copy checkpoint model weights
        shutil.copy(
            src=path.join(opts.outputDir, f"{target}_single-labeled_Fold-{best_fold}.model.weights.hdf5"),
            dst=path.join(opts.outputDir, f"{target}_single-labeled_Fold-{best_fold}.best.model.weights.hdf5"))
        # save complete model
        best_model = define_single_label_model(input_size=len(x[0]), opts=opts)
        best_model.load_weights(path.join(opts.outputDir,
                                          f"{target}_single-labeled_Fold-{best_fold}.model.weights.hdf5"))
        # create output directory and store complete model
        best_model.save(filepath=path.join(opts.outputDir, f"{target}_saved_model"))
        # # else:
        # logging.info("Your selected number of folds for Cross validation is out of range. "
        #              "It must be 1 or smaller than 1 hundredth of the number of samples.")
        # sys.exit("Number of folds out of range")

        # store the evaluation data of all trained models (all targets, all folds)
        (pd
         .concat(performance_list, ignore_index=True)
         .to_csv(path_or_buf=path.join(opts.outputDir, 'single_label_model.evaluation.csv'))
         )