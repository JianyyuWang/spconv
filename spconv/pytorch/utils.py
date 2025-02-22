# Copyright 2021 Yan Yan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import List
import torch
from cumm import tensorview as tv

from spconv.core_cc.csrc.sparse.all import SpconvOps
from spconv.pytorch.cppcore import torch_tensor_to_tv, get_current_stream


class PointToVoxel(object):
    """WARNING: you MUST construct PointToVoxel AFTER set device.
    """
    def __init__(self,
                 vsize_xyz: List[float],
                 coors_range_xyz: List[float],
                 num_point_features: int,
                 max_num_voxels: int,
                 max_num_points_per_voxel: int,
                 device: torch.device = torch.device("cpu:0")):
        self.ndim = len(vsize_xyz)

        self.device = device
        vsize, grid_size, grid_stride, coors_range = SpconvOps.calc_point2voxel_meta_data(
            vsize_xyz, coors_range_xyz)
        self.num_point_features = num_point_features
        self.max_num_voxels = max_num_voxels
        self.max_num_points_per_voxel = max_num_points_per_voxel
        self.vsize = vsize
        self.grid_size = grid_size
        self.grid_stride = grid_stride
        self.coors_range = coors_range

        self.voxels = torch.zeros(
            [max_num_voxels, max_num_points_per_voxel, num_point_features],
            dtype=torch.float32,
            device=device)
        self.indices = torch.zeros([max_num_voxels, self.ndim],
                                   dtype=torch.int32,
                                   device=device)
        self.num_per_voxel = torch.zeros([max_num_voxels],
                                         dtype=torch.int32,
                                         device=device)
        if device.type == "cpu":
            self.hashdata = torch.full(grid_size,
                                       -1,
                                       dtype=torch.int32,
                                       device=device)
            self.point_indice_data = torch.Tensor()
        else:
            self.hashdata = torch.empty([1, 2],
                                        dtype=torch.int64,
                                        device=device)
            self.point_indice_data = torch.empty([1],
                                                 dtype=torch.int64,
                                                 device=device)

    def __call__(self,
                 pc: torch.Tensor,
                 clear_voxels: bool = True,
                 empty_mean: bool = False):
        assert pc.device.type == self.device.type, "your pc device is wrong"
        expected_hash_data_num = pc.shape[0] * 2
        with torch.no_grad():
            if self.device.type != "cpu":
                if self.hashdata.shape[0] < expected_hash_data_num:
                    self.hashdata = torch.empty([expected_hash_data_num, 2],
                                                dtype=torch.int64,
                                                device=self.device)

                if self.point_indice_data.shape[0] < pc.shape[0]:
                    self.point_indice_data = torch.empty([pc.shape[0]],
                                                         dtype=torch.int64,
                                                         device=self.device)
                pc_tv = torch_tensor_to_tv(pc)
                stream = get_current_stream()
                voxels_tv = torch_tensor_to_tv(self.voxels)
                indices_tv = torch_tensor_to_tv(self.indices)
                num_per_voxel_tv = torch_tensor_to_tv(self.num_per_voxel)
                hashdata_tv = torch_tensor_to_tv(
                    self.hashdata,
                    dtype=tv.custom128,
                    shape=[self.hashdata.shape[0]])
                point_indice_data_tv = torch_tensor_to_tv(
                    self.point_indice_data)

                res = SpconvOps.point2voxel_cuda(
                    pc_tv, voxels_tv, indices_tv, num_per_voxel_tv,
                    hashdata_tv, point_indice_data_tv, self.vsize,
                    self.grid_size, self.grid_stride, self.coors_range,
                    empty_mean, clear_voxels, stream)
                num_voxels = res[0].shape[0]
            else:
                pc_tv = torch_tensor_to_tv(pc)
                stream = get_current_stream()
                voxels_tv = torch_tensor_to_tv(self.voxels)
                indices_tv = torch_tensor_to_tv(self.indices)
                num_per_voxel_tv = torch_tensor_to_tv(self.num_per_voxel)
                hashdata_tv = torch_tensor_to_tv(self.hashdata, dtype=tv.int32)
                res = SpconvOps.point2voxel_cpu(pc_tv, voxels_tv, indices_tv,
                                                num_per_voxel_tv, hashdata_tv,
                                                self.vsize, self.grid_size,
                                                self.grid_stride,
                                                self.coors_range, empty_mean,
                                                clear_voxels)
                num_voxels = res[0].shape[0]

            return (self.voxels[:num_voxels], self.indices[:num_voxels],
                    self.num_per_voxel[:num_voxels])
