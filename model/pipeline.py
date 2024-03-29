import scipy
import zarr
import torch
import scipy.sparse as sp
from scipy.linalg import fractional_matrix_power
from sklearn.neighbors import NearestNeighbors
import networkx as nx
import numpy as np
from tqdm import tqdm, trange
from sklearn.cluster import DBSCAN
from torch.utils.data import Dataset
import os
import sys

from utils.utils import (
    Gaussian,
    softmax,
    get_index_by_id_from_array,
    compute_neighbors,
    create_matrix,
    label_propagation,
    remove_ambiguous_label,
    zscore,
)


class Pipeline(Dataset):
    """Graph dataset object, loading dataset in a batch-wise manner.

    Args:
        zarr_dataset_path (string): path of zarr dataset root.
        k (int): k nearest neighbor used in neighbors.
        sigma (float): Gaussian variance when computing neighbors coefficient.
        use_neighbor_feature (boolean): whether to use the reconstructing 
            neighbors strategy.

    Each contig is stored into dictionary, with following keys:
        - feature: concated tnf and rpkm feature dim (104, 1);

    """
    # TODO: add feature per batch as comments.
    def __init__(
        self,
        zarr_dataset_path: str = "",
        k: int = 5,
        sigma: int =1,
        multisample=False,
        use_neighbor_feature=True,
        must_link_path: str = "",
        *args,
        **kwargs,
    ) -> None:
        super().__init__()
        self.zarr_dataset_path = zarr_dataset_path
        self.k = k
        self.use_neighbor_feature = use_neighbor_feature
        self.must_link_path = must_link_path
        self.multisample = multisample
        self.Gaussian = Gaussian(sigma=sigma)
        self.dbscan = DBSCAN(
            eps=1.65,
            metric="precomputed",
            min_samples=2,
            n_jobs=50
        )
        self.data = []

        self.data = self.load_dataset(zarr_dataset_path)

    def load_dataset(self, zarr_dataset_path):
        data_list, contig_id_list = self._load_graph_attrs(zarr_dataset_path)
        if self.use_neighbor_feature:
            data_list = self.create_knn_graph(
                data_list=data_list,
                k=self.k,
            )
        pre_compute_matrix = create_matrix(
            data_list=data_list,
            contig_list=contig_id_list,
        )
        
        # Get DBSCAN clustering result.
        cluster_result = self.dbscan.fit(pre_compute_matrix)
        labels_array = cluster_result.labels_
        print('finish dbscan')
        labels_array = label_propagation(labels_array, create_matrix(data_list=data_list, contig_list=contig_id_list, labels_array=labels_array, option='sparse'))
        print('finish lbp')
        data_list, labels_array = remove_ambiguous_label(data_list, labels_array, contig_id_list)
        print('finish remove_ambiguous_label')
        self.generate_must_link(data_list, output=self.must_link_path)
        data_list = self.neighbor_graph_to_training_set(data_list, contig_id_list, self.k)
        print('finish fiter_knn_graph')
        return data_list

    def generate_must_link(self, data_list, output=''):
        with open(output, 'w') as f:
            for data in data_list:
                for neigh in data['neighbors']:
                    id = int(data['id'])
                    f.write(f'{str(id)}\t{str(int(neigh))}\n')


    def __getitem__(self, index: int):
        return self.data[index]

    def __len__(self):
        return len(self.data)

    def _load_graph_attrs(self, zarr_dataset_path: str):
        root = zarr.open(zarr_dataset_path, mode="r")
        contig_id_list = root.attrs["contig_id_list"]
        tnf_list = root.attrs["tnf_list"]
        rpkm_list = root.attrs["rpkm_list"]
        # label_list = root.attrs["label_list"]

        data_list = []
        rkpm_array = np.array(rpkm_list, dtype="float32")
        tnf_array = np.array(tnf_list, dtype="float32")


        if self.multisample:
            zscore(rkpm_array, axis=0, inplace=True)
        zscore(tnf_array, axis=1, inplace=True)
        all_feature = np.concatenate((tnf_array, rkpm_array), axis=1)
        for i in tqdm(contig_id_list, desc="Loading data..."):
            item = {}
            idx = contig_id_list.index(i)
            feature = all_feature[idx]
            # labels = np.array([label_list[idx]], dtype="float32")
            contig_id = np.array([i], dtype="float32")
            item["feature"] = feature
            # item["labels"] = labels
            item["id"] = contig_id
            data_list.append(item)
        return data_list, contig_id_list

    def create_knn_graph(self, data_list, k, threshold=10):
        """Updates the k nearest neighbors for each contig in the dictionary. 
        
        Alerts: knn graph is created by id vector, stores the neightbors 
        and weights for each neighbor.

        Args:
            data_list (list): list format dataset.

        Returns:
            data_list (list): list format dataset.
        """
        if self.multisample:
            threshold = 6
        Gau = Gaussian()
        id_list = []
        feature_list = []
        for i in range(len(data_list)):
            feature_list.append(data_list[i]["feature"])
            id_list.append(data_list[i]["id"])
        
        feature_array = np.array(feature_list, dtype="float32")
        nbrs = NearestNeighbors(n_neighbors=k+1, algorithm='auto',  metric='euclidean', n_jobs=50).fit(feature_array)
        distances, indices = nbrs.kneighbors(feature_array)
        for i in trange(feature_array.shape[0], desc="Creating KNN graph..."):
            neighbors_array = indices[i][1:]
            distance_array = np.power(distances[i][1:], 2)
            invalid_idx = np.where(distance_array >= threshold)
            distance_array = np.delete(distance_array, invalid_idx)
            neighbors_array = np.delete(neighbors_array, invalid_idx)
            for idx, neight_idx in enumerate(neighbors_array): neighbors_array[idx] = id_list[neight_idx]
            data_list[i]["neighbors"] = np.float32(neighbors_array)
            data_list[i]["distances"] = np.float32(distance_array)
        return data_list


    def neighbor_graph_to_training_set(self, data_list, contig_id_list, k):
        # filter the knn graph and create neighbor features and neighbors mask instead.
        node_num = len(data_list)
        gau = Gaussian(sigma=1.0)
        featre_shape = data_list[0]["feature"].shape

        for i in trange(node_num, desc="Generate neighbor trainning set..."):
            neighbors_array = data_list[i]["neighbors"]
            weights_array = data_list[i]["distances"]
            del data_list[i]["neighbors"]
            del data_list[i]["distances"]
            neighbors_num = neighbors_array.shape[0]
            neighbors_indices = [contig_id_list.index(neigh_id) for neigh_id in neighbors_array]
            neighbors_feature = [data_list[neigh_idx]["feature"] for neigh_idx in neighbors_indices]
            neighbors_feature.extend([np.zeros(featre_shape, dtype="float32") for _ in range(k - neighbors_num)])
            if neighbors_num == 0:
                neighbors_weight = np.array([0.0, 0.0, 0.0], dtype="float32")
            elif neighbors_num == 1:
                neighbors_weight = np.array([1.0, 0.0, 0.0], dtype="float32")
            else:
                neighbors_weight = gau.cal_coefficient(weights_array)
                neighbors_weight = softmax(neighbors_weight)
                neighbors_weight = np.concatenate((neighbors_weight, np.array([0.0] * (k - neighbors_num), dtype="float32")), axis=0)
            data_list[i]["neighbors_feature"] = np.stack(neighbors_feature)
            data_list[i]["neighbors_feature_mask"] = np.array([1.0] * neighbors_num + [0.0] * (k - neighbors_num), dtype="float32")
            data_list[i]["neighbors_weight"] = neighbors_weight
        return data_list

