# %%
from __future__ import annotations

import numpy as np
import pandas as pd
import torch
from matbench.metadata import mbv01_metadata
from matbench.task import MatbenchTask

from aviary.data import InMemoryDataLoader
from aviary.wrenformer.data import collate_batch, wyckoff_embedding_from_aflow_str
from aviary.wrenformer.model import Wrenformer
from examples.mat_bench import DATA_PATHS

__author__ = "Janosh Riebesell"
__date__ = "2022-04-12"


# %%
model_name = "wrenformer"

# deploy Wren on structure tasks only
structure_datasets = [
    k for k, v in mbv01_metadata.items() if v.input_type == "structure"
]


# %%
dataset_name = "matbench_jdft2d"
matbench_task = MatbenchTask(dataset_name, autoload=False)

df = pd.read_json(DATA_PATHS[dataset_name]).set_index("mbid", drop=False)
df["features"] = df.wyckoff.map(wyckoff_embedding_from_aflow_str)

matbench_task.df = df
target = matbench_task.metadata.target
task_type = matbench_task.metadata.task_type
task_dict = {target: task_type}


# %%
model = Wrenformer(
    robust=False,
    n_targets=[1 if task_type == "regression" else 2],
    n_features=200 + 444 + 1,
    task_dict=task_dict,
)
device = "cuda" if torch.cuda.is_available() else "cpu"
model.to(device)
print(f"Pytorch running on {device=}")


learning_rate = 1e-3
optimizer = torch.optim.AdamW(params=model.parameters(), lr=learning_rate)
warmup_steps = 10


def lr_lambda(epoch: int) -> float:
    return min((epoch + 1) ** (-0.5), (epoch + 1) * warmup_steps ** (-1.5))


scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)


# %%
features, targets, ids = (df[x] for x in ["features", target, "mbid"])
targets = torch.tensor(targets, device=device)
features = tuple(tensor.to(device) for tensor in features)

fold = 0
train_df = matbench_task.get_train_and_val_data(fold, as_type="df")
test_df = matbench_task.get_test_data(fold, as_type="df", include_target=True)

features, targets, ids = (train_df[x] for x in ["features", target, "mbid"])
targets = torch.tensor(targets, device=device)
inputs = np.empty(len(features), dtype=object)
for idx, tensor in enumerate(features):
    inputs[idx] = tensor.to(device)

train_loader = InMemoryDataLoader(
    [inputs, targets, ids],
    batch_size=32,
    shuffle=True,
    collate_fn=collate_batch,
)


# %%
features, targets, ids = (test_df[x] for x in ["features", target, "mbid"])
targets = torch.tensor(targets, device=device)
inputs = np.empty(len(features), dtype=object)
for idx, tensor in enumerate(features):
    inputs[idx] = tensor.to(device)

test_loader = InMemoryDataLoader(
    [inputs, targets, ids], batch_size=1024, collate_fn=collate_batch
)

# get test set predictions
model.eval()
with torch.no_grad():
    predictions = torch.cat([model(*inputs)[0].squeeze() for inputs, *_ in test_loader])
