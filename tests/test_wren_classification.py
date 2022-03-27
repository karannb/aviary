import os

import numpy as np
import torch
from matminer.utils.io import load_dataframe_from_json
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split as split

from aviary.utils import results_multitask, train_ensemble
from aviary.wren.data import WyckoffData, collate_batch
from aviary.wren.model import Wren
from aviary.wren.utils import get_aflow_label_spglib

torch.manual_seed(0)  # ensure reproducible results


def test_wren_clf():
    data_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data/matbench_phonons.json.gz"
    )
    elem_emb = "matscholar200"
    sym_emb = "bra-alg-off"
    targets = ["phdos_clf"]
    tasks = ["classification"]
    losses = ["CSE"]
    robust = True
    model_name = "wren-clf-test"
    elem_fea_len = 32
    sym_fea_len = 32
    n_graph = 3
    ensemble = 2
    run_id = 1
    data_seed = 42
    epochs = 15
    log = False
    sample = 1
    test_size = 0.2
    resume = False
    fine_tune = None
    transfer = None
    optim = "AdamW"
    learning_rate = 3e-4
    momentum = 0.9
    weight_decay = 1e-6
    batch_size = 128
    workers = 0
    device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")

    task_dict = dict(zip(targets, tasks))
    loss_dict = dict(zip(targets, losses))

    assert os.path.exists(data_path), f"{data_path} does not exist!"

    df = load_dataframe_from_json(data_path)
    df["wyckoff"] = df.structure.apply(get_aflow_label_spglib)
    df["material_id"] = [f"mb_phdos_{i}" for i in range(len(df))]
    df["composition"] = df.structure.apply(
        lambda x: x.composition.formula.replace(" ", "")
    )
    df["phdos_clf"] = np.where((df["last phdos peak"] > 450), 1, 0)

    dataset = WyckoffData(
        df=df, elem_emb=elem_emb, sym_emb=sym_emb, task_dict=task_dict
    )
    n_targets = dataset.n_targets
    elem_emb_len = dataset.elem_emb_len
    sym_emb_len = dataset.sym_emb_len

    train_idx = list(range(len(dataset)))

    print(f"using {test_size} of training set as test set")
    train_idx, test_idx = split(train_idx, random_state=data_seed, test_size=test_size)
    test_set = torch.utils.data.Subset(dataset, test_idx)

    print("No validation set used, using test set for evaluation purposes")
    # NOTE that when using this option care must be taken not to
    # peak at the test-set. The only valid model to use is the one
    # obtained after the final epoch where the epoch count is
    # decided in advance of the experiment.
    val_set = test_set

    train_set = torch.utils.data.Subset(dataset, train_idx[0::sample])

    data_params = {
        "batch_size": batch_size,
        "num_workers": workers,
        "pin_memory": False,
        "shuffle": True,
        "collate_fn": collate_batch,
    }

    setup_params = {
        "optim": optim,
        "learning_rate": learning_rate,
        "weight_decay": weight_decay,
        "momentum": momentum,
        "device": device,
    }

    restart_params = {
        "resume": resume,
        "fine_tune": fine_tune,
        "transfer": transfer,
    }

    model_params = {
        "task_dict": task_dict,
        "robust": robust,
        "n_targets": n_targets,
        "elem_emb_len": elem_emb_len,
        "sym_emb_len": sym_emb_len,
        "elem_fea_len": elem_fea_len,
        "sym_fea_len": sym_fea_len,
        "n_graph": n_graph,
        "elem_heads": 2,
        "elem_gate": [256],
        "elem_msg": [256],
        "cry_heads": 2,
        "cry_gate": [256],
        "cry_msg": [256],
        "out_hidden": [256],
        "trunk_hidden": [64],
    }

    os.makedirs(f"models/{model_name}", exist_ok=True)

    train_ensemble(
        model_class=Wren,
        model_name=model_name,
        run_id=run_id,
        ensemble_folds=ensemble,
        epochs=epochs,
        train_set=train_set,
        val_set=val_set,
        log=log,
        data_params=data_params,
        setup_params=setup_params,
        restart_params=restart_params,
        model_params=model_params,
        loss_dict=loss_dict,
    )

    data_params["batch_size"] = 64 * batch_size  # faster model inference
    data_params["shuffle"] = False  # need fixed data order due to ensembling

    results_dict = results_multitask(
        model_class=Wren,
        model_name=model_name,
        run_id=run_id,
        ensemble_folds=ensemble,
        test_set=test_set,
        data_params=data_params,
        robust=robust,
        task_dict=task_dict,
        device=device,
        eval_type="checkpoint",
        save_results=False,
    )

    logits = results_dict["phdos_clf"]["logits"]
    target = results_dict["phdos_clf"]["target"]

    # calculate metrics and errors with associated errors for ensembles
    ens_logits = np.mean(logits, axis=0)

    target_ohe = np.zeros_like(ens_logits)
    target_ohe[np.arange(target.size), target] = 1

    ens_acc = accuracy_score(target, np.argmax(ens_logits, axis=1))
    ens_roc_auc = roc_auc_score(target_ohe, ens_logits)

    assert ens_acc > 0.85
    assert ens_roc_auc > 0.9


if __name__ == "__main__":
    test_wren_clf()
