import os

from copy import deepcopy
from typing import List, Union, Dict, Any

from allennlp.common import Params
from allennlp.common.tee_logger import TeeLogger
from allennlp.common.util import prepare_environment
from allennlp.data import Vocabulary, Dataset, DataIterator, DatasetReader, Tokenizer, TokenIndexer
from allennlp.models import Model, archive_model
from allennlp.training import Trainer
from retrieval.fever_doc_db import FeverDocDB
from rte.parikh.reader import FEVERReader

import argparse
import logging
import sys
import json

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name

def train_model(db: FeverDocDB, params: Union[Params, Dict[str, Any]], args: argparse.Namespace , serialization_dir: str) -> Model:
    """
    This function can be used as an entry point to running models in AllenNLP
    directly from a JSON specification using a :class:`Driver`. Note that if
    you care about reproducibility, you should avoid running code using Pytorch
    or numpy which affect the reproducibility of your experiment before you
    import and use this function, these libraries rely on random seeds which
    can be set in this function via a JSON specification file. Note that this
    function performs training and will also evaluate the trained model on
    development and test sets if provided in the parameter json.

    Parameters
    ----------
    params: Params, required.
        A parameter object specifying an AllenNLP Experiment.
    serialization_dir: str, required
        The directory in which to save results and logs.
    """
    prepare_environment(params)

    os.makedirs(serialization_dir, exist_ok=True)
    sys.stdout = TeeLogger(os.path.join(serialization_dir, "stdout.log"), sys.stdout)  # type: ignore
    sys.stderr = TeeLogger(os.path.join(serialization_dir, "stderr.log"), sys.stderr)  # type: ignore
    handler = logging.FileHandler(os.path.join(serialization_dir, "python_logging.log"))
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
    logging.getLogger().addHandler(handler)
    serialization_params = deepcopy(params).as_dict(quiet=True)

    with open(os.path.join(serialization_dir, "model_params.json"), "w") as param_file:
        json.dump(serialization_params, param_file, indent=4)

    # Now we begin assembling the required parts for the Trainer.
    dataset_reader = FEVERReader(db,
                                 tokenizer=Tokenizer.from_params(params.pop('tokenizer', {})),
                                 token_indexers=TokenIndexer.dict_from_params(params.pop('token_indexers', {})))

    train_data_path = params.pop('train_data_path')
    logger.info("Reading training data from %s", train_data_path)
    train_data = dataset_reader.read(train_data_path)

    all_datasets: List[Dataset] = [train_data]
    datasets_in_vocab = ["train"]

    validation_data_path = params.pop('validation_data_path', None)
    if validation_data_path is not None:
        logger.info("Reading validation data from %s", validation_data_path)
        validation_data = dataset_reader.read(validation_data_path)
        all_datasets.append(validation_data)
        datasets_in_vocab.append("validation")
    else:
        validation_data = None

    logger.info("Creating a vocabulary using %s data.", ", ".join(datasets_in_vocab))
    vocab = Vocabulary.from_params(params.pop("vocabulary", {}),
                                   Dataset([instance for dataset in all_datasets
                                            for instance in dataset.instances]))
    vocab.save_to_files(os.path.join(serialization_dir, "vocabulary"))

    model = Model.from_params(vocab, params.pop('model'))
    iterator = DataIterator.from_params(params.pop("iterator"))

    train_data.index_instances(vocab)
    if validation_data:
        validation_data.index_instances(vocab)

    trainer_params = params.pop("trainer")
    trainer = Trainer.from_params(model,
                                  serialization_dir,
                                  iterator,
                                  train_data,
                                  validation_data,
                                  trainer_params)

    params.assert_empty('base train command')
    trainer.train()

    # Now tar up results
    archive_model(serialization_dir)

    return model



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('db', type=str, help='/path/to/saved/db.db')
    parser.add_argument('param_path',
                           type=str,
                           help='path to parameter file describing the model to be trained')

    cuda_device = parser.add_mutually_exclusive_group(required=False)
    cuda_device.add_argument('--cuda-device', type=int, default=-1, help='id of GPU to use (if any)')
    cuda_device.add_argument('--cuda_device', type=int, help=argparse.SUPPRESS)


    parser.add_argument('-o', '--overrides',
                           type=str,
                           default="",
                           help='a HOCON structure used to override the experiment configuration')


    args = parser.parse_args()

    db = FeverDocDB(args.db)

    params = Params.from_file(args.param_path)
    train_model(db,params,args,"logs")