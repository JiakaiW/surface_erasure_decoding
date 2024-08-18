from dataclasses import dataclass, field
from typing import List, Union, Tuple, Optional, Iterable
import stim
import numpy as np
from abc import ABC, abstractmethod
from itertools import islice

def chunked(iterable, chunk_size):
    """Yield successive n-sized chunks from the input iterable."""
    it = iter(iterable)
    while True:
        chunk = list(islice(it, chunk_size))
        if not chunk:
            break
        yield chunk


Etype_to_stim_target_fun = {
    'X': stim.target_x,
    'Y': stim.target_y,
    'Z': stim.target_z,
}


@dataclass
class SQE: #stands for Single_qubit_error/event
    """
    Determines what's the type of error on the data qubit (I,X,Y,Z) and if it's heralded
    """
    type: str
    heralded: bool
    def __post_init__(self):
        assert self.type in ['I','X','Y','Z'] 

@dataclass
class MQE: #stands for Multi_qubit_error/event
    """
    Constitutes one of the many disjoint probabilities in an Error_mechanism
    """
    p: float
    list_of_SQE: List[SQE]
    def __post_init__(self):
        assert self.p >= 0, "can't create an event with below 0 probability?"

@dataclass
class InstructionVectorizer(ABC):
    list_of_MQE: List[MQE]

    def __post_init__(self):
        sum_of_prob = sum([mqe.p for mqe in self.list_of_MQE])
        assert  sum_of_prob > 1-1e-7 and sum_of_prob < 1+ 1e-7
        self.num_qubits = len(self.list_of_MQE[0].list_of_SQE) # the number of data qubits it describes
        assert all(len(mqe.list_of_SQE) == self.num_qubits for mqe in self.list_of_MQE) # Ensure the error model describes errors on a fixed amount of data qubits

    @abstractmethod
    def get_instruction(self, qubits:List[int]) -> List:
        pass   

    def __repr__(self):
        s = ''
        for mqe in self.list_of_MQE:
            s += str(mqe)
            s += '\n'
        return s

@dataclass
class InstructionVectorizerWithStepWiseProbs(InstructionVectorizer):
    def __post_init__(self):
        # Convert the probabilities to those used in CORRELATED_ERROR and ELSE_CORRELATED_ERRORs (used for non-vectorized methods only, which are Deprecated)
        self.list_of_MQE = sorted(self.list_of_MQE,reverse=True, key=lambda x: x.p)
        self.stepwise_probabilities = []
        prob_left = 1
        for event in self.list_of_MQE:
            stepwise_p = event.p / prob_left
            stepwise_p= max(min(stepwise_p, 1), 0)
            self.stepwise_probabilities.append(stepwise_p)
            prob_left -= event.p

@dataclass
class InstructionVectorizerWithPosteriorProbs(InstructionVectorizer):
    def __post_init__(self):
        # Prepare conditional probabilities for decoding erasure detection 
        # compute the arithmatic sum of I/X/Y/Z error rates over num_qubits qubits (using the sum without heralding in normal circuit is the same as using the *disjoint* components without heralding)
        #   also compute the sum of heralded pauli errors for num_qubits qubits
        self.Etype_to_sum = [None] * self.num_qubits
        self.Etype_to_heralded_sum = [None] * self.num_qubits
        self.conditional_probabilities = [None] * self.num_qubits
        self.p_herald = [0] * self.num_qubits
        for i in range(self.num_qubits):
            self.Etype_to_sum[i] = {
                'I':0,
                'X':0,
                'Y':0,
                'Z':0
            }
            self.Etype_to_heralded_sum[i] = {
                'I':0,
                'X':0,
                'Y':0,
                'Z':0
            }
            for mqe in self.list_of_MQE:
                Etype_on_this_qubit = mqe.list_of_SQE[i].type
                self.Etype_to_sum[i][Etype_on_this_qubit] += mqe.p
                if mqe.list_of_SQE[i].heralded == True:
                    self.Etype_to_heralded_sum[i][Etype_on_this_qubit] += mqe.p

            # Compute the conditional probabilities
            self.p_herald[i] = sum(self.Etype_to_heralded_sum[i].values())
            if self.p_herald[i] != 0:
                self.conditional_probabilities[i] = [
                    self.Etype_to_heralded_sum[i]['X']/self.p_herald[i], 
                    self.Etype_to_heralded_sum[i]['Y']/self.p_herald[i], 
                    self.Etype_to_heralded_sum[i]['Z']/self.p_herald[i],
                    (self.Etype_to_sum[i]['X']-self.Etype_to_heralded_sum[i]['X'])/(1-self.p_herald[i]),
                    (self.Etype_to_sum[i]['Y']-self.Etype_to_heralded_sum[i]['Y'])/(1-self.p_herald[i]),
                    (self.Etype_to_sum[i]['Z']-self.Etype_to_heralded_sum[i]['Z'])/(1-self.p_herald[i])
                ]
        self.heralded_locations_one_hot_encoding  = [prob > 0 for prob in self.p_herald]
        self.herald_locations = np.where(self.heralded_locations_one_hot_encoding)[0]
        self.num_herald_locations = np.sum(self.heralded_locations_one_hot_encoding)

    def get_new_ancillas_array_update_list(self,data_qubits_array,index_in_list):
        num_parallel = data_qubits_array.shape[1]
        num_ancillas= num_parallel * self.num_herald_locations
        
        ancillas = np.zeros(data_qubits_array.shape, dtype=int)
        counter = index_in_list[0]
        fill_values = np.arange(counter, counter + num_ancillas)
        fill_values = fill_values.reshape(num_parallel, self.num_herald_locations).T
        ancillas[self.herald_locations, :] = fill_values
        index_in_list[0] += num_ancillas
        return ancillas
    
@dataclass
class NormalInstructionVectorizer(InstructionVectorizerWithStepWiseProbs):
    instruction_name: Optional[str] = field(default=None)
    instruction_arg: Union[float, Iterable[float]] = field(default=None)
    vectorizable: Optional[bool]
    
    def __post_init__(self):
        InstructionVectorizer.__post_init__(self)
        if  self.instruction_name == None or self.instruction_arg == None:
            self.vectorizable = False
            InstructionVectorizerWithStepWiseProbs.__post_init__(self)
        else:
            self.vectorizable = True

    def get_instruction(self, qubits:List[int]) -> List:
        assert len(qubits) % self.num_qubits == 0, "wrong number of qubits"
        if self.vectorizable:
            list_of_args = []
            list_of_args.append([self.instruction_name,qubits,self.instruction_arg])
            return list_of_args
        else:
            list_of_args_gathered = []
            for sets_of_qubits in chunked(iterable=qubits, chunk_size = self.num_qubits):
                list_of_args = []
                for mqe, stepwise_p in zip(self.list_of_MQE,self.stepwise_probabilities):
                    targets = []
                    for sqe,qubit in zip(mqe.list_of_SQE, sets_of_qubits):# the len of qubits can be smaller than len of event.list_of_Etype, but the smallest len count will determin how many iterations are run in zip()
                        if sqe.type != 'I':
                            targets.append(Etype_to_stim_target_fun[sqe.type](qubit))
                    list_of_args.append(["ELSE_CORRELATED_ERROR",targets,stepwise_p])
                list_of_args[0][0] = "CORRELATED_ERROR"
                list_of_args_gathered.extend(list_of_args)
            return list_of_args_gathered   

@dataclass
class ErasureInstructionVectorizer(InstructionVectorizerWithStepWiseProbs, InstructionVectorizerWithPosteriorProbs):
    '''
        Whether an erasure insturction is vectorizable is dependent on whether there's correlation between two data qubits.
        If during a 2-qubit gate, the two qubits are independently erased, then we apply two pairs of PAULI_CHANNEL_2.
        But if the 2-qubit gate is described by some correlated erasure error, as in PHYSICAL REVIEW X 13, 041013 (2023), 
            then the mechanism involving more than 2 qubits need to be modeled by CORRELATED_ERROR, and CORRELATED_ERROR are not vectorizable.

        instruction_name and instruction_arg can be used to describe one PAULI_CHANNEL_2 that is applied to one (pair (with broadcasting)) of qubits
            or two PAULI_CHANNEL_2 that is separatly applied to a pair of qubits
    '''
    instruction_name: Optional[Union[str, Iterable[float]]] = field(default=None)
    instruction_arg: Optional[Union[float, Iterable[float],Iterable[Iterable[float]]]] = field(default=None)
    vectorizable: Optional[bool]
    def __post_init__(self):
        InstructionVectorizer.__post_init__(self)
        if self.instruction_name == None or self.instruction_arg == None:
            self.vectorizable = False

            InstructionVectorizerWithStepWiseProbs.__post_init__(self)
        else:
            self.vectorizable = True

            assert np.array(self.instruction_name).shape[0] == np.array(self.instruction_arg).shape[0]
            InstructionVectorizerWithPosteriorProbs.__post_init__(self)

    def get_instruction(self, qubits:List[int],next_ancilla_qubit_index_in_list:List[int]) -> List:
        assert len(qubits) % self.num_qubits == 0, "wrong number of qubits"
        data_qubits_array = np.array(qubits).reshape(-1,self.num_qubits).T
        ancillas = self.get_new_ancillas_array_update_list(data_qubits_array,next_ancilla_qubit_index_in_list)

        if self.vectorizable:
            list_of_args = []
            list_of_args.append([self.instruction_name,qubits,self.instruction_arg])
            return list_of_args
            # TODO: not finished yet
        else:
            ancillas = ancillas.T.flatten()
            ancilla_idx = 0
            # Step-2 append instructions        
            list_of_args_gathered = []
            for sets_of_qubits in chunked(iterable=qubits, chunk_size = self.num_qubits):
                list_of_args = []
                for mqe, stepwise_p in zip(self.list_of_MQE,self.stepwise_probabilities):
                    targets = []
                    for sqe,circuit_qubit_idx in zip(mqe.list_of_SQE, sets_of_qubits):
                        if sqe.type != 'I':
                            targets.append(Etype_to_stim_target_fun[sqe.type](circuit_qubit_idx))
                        if sqe.heralded:# If it heralded, then the corresponding ancilla must have been assigned on this qubit for this Gate_error_model 
                            targets.append(Etype_to_stim_target_fun['X'](ancillas[ancilla_idx]))
                            ancilla_idx += 1
                    list_of_args.append(["ELSE_CORRELATED_ERROR",targets,stepwise_p])
                list_of_args[0][0] = "CORRELATED_ERROR" # The first instruction should be CORRELATED_ERROR in stim
                list_of_args_gathered.extend(list_of_args)
            return list_of_args_gathered   


@dataclass
class DynamicInstructionVectorizer(InstructionVectorizerWithPosteriorProbs):
    def __post_init__(self):
        InstructionVectorizer.__post_init__(self)
        InstructionVectorizerWithPosteriorProbs.__post_init__(self)

    def get_instruction(self,qubits:List[int],erasure_measurement_index_in_list:List[int]) -> List:
        assert len(qubits) % self.num_qubits == 0, "wrong number of qubits"
        data_qubits_array = np.array(qubits).reshape(-1,self.num_qubits).T

        if self.num_herald_locations > 0:
            ancillas = self.get_new_ancillas_array_update_list(data_qubits_array,erasure_measurement_index_in_list)
            
            # Get bool array signifing erasure detection
            erasure_meas = np.zeros(ancillas.shape, dtype=bool)
            erasure_meas[self.herald_locations, :] = self.single_measurement_sample[ancillas[self.herald_locations, :]]
            
            # For each qubit location, get two conditional probabilities array or the static probability if erasure conversion not used. 
            list_of_args = []
            for i in range(self.num_qubits): # This looks like a loop, but this loop is at most length-2. It's still parrallized
                if i in self.herald_locations: 
                    converted_data_qubits = data_qubits_array[i][np.where(erasure_meas[i])[0]]
                    no_detection_data_qubits = data_qubits_array[i][np.where(~erasure_meas[i])[0]]
                    list_of_args.append(["PAULI_CHANNEL_1", converted_data_qubits, [self.conditional_probabilities[i][0], self.conditional_probabilities[i][1], self.conditional_probabilities[i][2]]])
                    list_of_args.append(["PAULI_CHANNEL_1", no_detection_data_qubits, [self.conditional_probabilities[i][3], self.conditional_probabilities[i][4], self.conditional_probabilities[i][5]]])
                else:
                    list_of_args.append(["PAULI_CHANNEL_1", data_qubits_array[i], [self.Etype_to_sum[i]['X'], self.Etype_to_sum[i]['Y'], self.Etype_to_sum[i]['Z']]])
        else:
            list_of_args = []
            for i in range(self.num_qubits):
                list_of_args.append(["PAULI_CHANNEL_1", data_qubits_array[i], [self.Etype_to_sum[i]['X'], self.Etype_to_sum[i]['Y'], self.Etype_to_sum[i]['Z']]])
        return list_of_args   
    
@dataclass
class DeterministicInstructionVectorizer(InstructionVectorizer):
    def get_instruction(self, qubits: Union[List[int], Tuple[int]]) -> List:
        pass   

@dataclass
class Error_mechanism:
    """
    A Gate_error_mechanism constitutes multiple MQEs, can describe the intrinsic error or the erasure conversion of the gate.
    This class specify some error events with *disjoint* probabilities. 
    The MQEs are disjoint because it's good enough approximation to neglect events where multiple MQEs happening at the same time.
    It also specify which error events can be heralded, and herald rate.

    When used in gen_erasure_conversion_circuit, the error model is fully implemented
    When used in gen_normal_circuit, the error model is used without implementing heralding (operator on the last (or last two) relative qubit index is neglected)
    When used in gen_dynamic_circuit, the contidional arithmatically summed probabilities are used, and without implementing heralding

    It is made up of a continuous chunk of CORRELATED_ERROR and ELSE_CORRELATED_ERRORs. 
    The parameters used in CORRELATED_ERROR and ELSE_CORRELATED_ERRORs are different from what's given to an Error_mechanism. 
        Error_mechanism converts those probabilities to the type used by CORRELATED_ERROR and ELSE_CORRELATED_ERRORs. See documentation of stim.



    Vectorization:
        different modes have different vectorization rules.
        | Type              | normal mode               |   erasure mode                |   dynamic mode            |   deterministic mode      |
        |-------------------|---------------------------|-------------------------------|---------------------------|---------------------------|
        |1-q nonherald      |Error,q,param              |Error,q,param                  |Error,q,param              |Error,q,param'             |
        |1-q herald         |Error,q,param              |PAULI_CHANNEL_2,[q,a],param    |PAULI_CHANNEL_1,q,param'   |PAULI_CHANNEL_2,q,param'   |
        |2-q nonherald      |Error,[q,p],param          |Error,[q,p],param              |Error,[q,p],param          |Error,[q,p],param'         |
        |2-q herald         |Error,[q,p],param          |PAULI_CHANNEL_2,[q,a],param *2 |PAULI_CHANNEL_1,q,param'*2 |PAULI_CHANNEL_2,[q,p],param'| (there's no heralded 2-qubit errors, decompose them into 1-q heralds)
    """
    normal_vectorizor: NormalInstructionVectorizer
    erasure_vectorizor: ErasureInstructionVectorizer
    dynamic_vectorizor: DynamicInstructionVectorizer
    deterministic_vectorizor: DeterministicInstructionVectorizer

    next_ancilla_qubit_index_in_list: Optional[int] = None
    erasure_measurement_index_in_list: Optional[int] = None
    single_measurement_sample:  Optional[Union[List,np.array]] = None

    def __post_init__(self):
        
        if sum(self.dynamic_vectorizor.p_herald) == 0:
            self.is_erasure = False
        else:
            self.is_erasure = True

    def get_instruction(self, 
                        qubits: Union[List[int], Tuple[int]],
                        mode:str):
        '''
        return list of args that can be used in  stim.circuit.append()

        This function is a newer implementation of generating instructions with posterior probabilities. 
        The vectorization is over a batch of operations. For example, rather than doing one CNOT at a time, this vectorized method 
            generates one batch of CNOT instructions at a time.
        Accept qubits in the style of stim instructions, len(qubits) == integer multiple of self.num_qubits

        # TODO: do I have issue with this:
        # fill_values = fill_values.reshape(num_parallel, self.num_herald_locations).T # is this working? 
        fill_values = fill_values.reshape( self.num_herald_locations,num_parallel) # What used to work before I change to data_qubits_array = np.array(qubits).reshape(-1,self.num_qubits).T
        '''
        

        if mode == 'normal': 
            return self.normal_vectorizor.get_instruction(qubits=qubits)
        elif mode == 'erasure':
            return self.erasure_vectorizor.get_instruction(qubits,self.next_ancilla_qubit_index_in_list)
        elif mode == 'dynamic':
            return self.dynamic_vectorizor.get_instruction(qubits,self.erasure_measurement_index_in_list)
        elif mode == 'deterministic':
            return self.deterministic_vectorizor.get_instruction(qubits)
        else:
            raise Exception("unsupported mode")



@dataclass
class Gate_error_model:
    """
    A Gate_error_model contains one or more Gate_error_mechanisms,
    Different Gate_error_mechanisms within a Gate_error_model are considered independent (they can happen at the same time at higher order probability)

    A Gate_error_model is used to describe erasure conversion and normal error mechannism are independent,
    A Gate_error_model can only have one erasure conversion mechanism.
    """

    list_of_mechanisms: List[Error_mechanism]

    def __post_init__(self):
        if len(self.list_of_mechanisms) == 0:
            self.trivial = True
            return
        else:
            self.trivial = False
            
        assert all([mechanism.num_qubits == self.list_of_mechanisms[0].num_qubits for mechanism in self.list_of_mechanisms])
        assert sum([mechanism.is_erasure for mechanism in self.list_of_mechanisms]) <= 1

    
    def set_next_ancilla_qubit_index_in_list(self,next_ancilla_qubit_index_in_list: int):
        for mechanism in self.list_of_mechanisms:
            mechanism.next_ancilla_qubit_index_in_list = next_ancilla_qubit_index_in_list
    def set_erasure_measurement_index_in_list(self,erasure_measurement_index_in_list: int):
        for mechanism in self.list_of_mechanisms:
            mechanism.erasure_measurement_index_in_list = erasure_measurement_index_in_list
    def set_single_measurement_sample(self,single_measurement_sample: Union[List,np.array]):
        for mechanism in self.list_of_mechanisms:
            mechanism.single_measurement_sample = single_measurement_sample
    
    def get_instruction(self, 
                        qubits: Union[List[int], Tuple[int]],
                        mode:str):
        if self.trivial:
            return []
        list_of_args = []
        for mechanism in self.list_of_mechanisms:
            list_of_args += mechanism.get_instruction(qubits=qubits,mode=mode)
        return list_of_args
    
    def get_dynamic_instruction_vectorized(self,
                                           qubits:Union[List[int], Tuple[int]]):
        if self.trivial:
            return []
        list_of_args = []
        for mechanism in self.list_of_mechanisms:
            list_of_args.extend(mechanism.get_dynamic_instruction_vectorized(qubits=qubits))
        return list_of_args
    
    def __repr__(self) -> str:
        s = '''Gate_error_model of the following mechanisms:\n'''
        for i,mech in enumerate(self.list_of_mechanisms):
            s += f"mechanism {i}: \n"
            s += mech.__repr__()
            
        return  s




def get_1q_depolarization_mechanism(p_p):
    return Error_mechanism(
        vectorization= ('DEPOLARIZE1',p_p),
        list_of_MQE= 
        [   MQE(1-p_p,[SQE("I",False)]),
            MQE(p_p/3,[SQE("X",False)]),
            MQE(p_p/3,[SQE("Y",False)]),
            MQE(p_p/3,[SQE("Z",False)])
        ]
    )

def get_1q_differential_shift_mechanism(p_z_shift):
    return Error_mechanism(
        vectorization= ('Z_ERROR',p_z_shift),
        list_of_MQE= 
        [   
            MQE(1-p_z_shift,[SQE("I",False)]),
            MQE(p_z_shift,[SQE("Z",False)])
        ]
)

def get_1q_biased_erasure_mechanism(p_e):
    return Error_mechanism(
        vectorization= ("PAULI_CHANNEL_2", [i, ancilla], [
                  # ix iy iz
                    p_e / 2, 0, 0,
                  # xi xx xy xz
                    0, 0, 0, 0,
                  # yi yx yy yz
                    0, 0, 0, 0,
                  # zi zx zy zz
                    0, p_e / 2, 0, 0
                ]),
        list_of_MQE= 
        [   
            MQE(1 - p_e,[SQE("I",False)]),
            MQE(p_e/2,[SQE("I",True)]),
            MQE(p_e/2,[SQE("Z",True)])
        ]
)

def get_1q_error_model(p_e,p_z_shift, p_p):
    mechanism_list = [get_1q_depolarization_mechanism(p_p)]
    if p_z_shift>0:
        mechanism_list.append(get_1q_differential_shift_mechanism(p_z_shift))
    if p_e>0:
        mechanism_list.append(get_1q_biased_erasure_mechanism(p_e))
    return Gate_error_model(mechanism_list)




def get_2q_depolarization_mechanism(p_p):
    return Error_mechanism(
        vectorization= ('DEPOLARIZE2',p_p),
        list_of_MQE= 
        [   MQE(1-p_p,[SQE("I",False),SQE("I",False)]),

            MQE(p_p/15,[SQE("I",False),SQE("X",False)]),
            MQE(p_p/15,[SQE("I",False),SQE("Y",False)]),
            MQE(p_p/15,[SQE("I",False),SQE("Z",False)]),

            MQE(p_p/15,[SQE("X",False),SQE("I",False)]),
            MQE(p_p/15,[SQE("X",False),SQE("X",False)]),
            MQE(p_p/15,[SQE("X",False),SQE("Y",False)]),
            MQE(p_p/15,[SQE("X",False),SQE("Z",False)]),

            MQE(p_p/15,[SQE("Y",False),SQE("I",False)]),
            MQE(p_p/15,[SQE("Y",False),SQE("X",False)]),
            MQE(p_p/15,[SQE("Y",False),SQE("Y",False)]),
            MQE(p_p/15,[SQE("Y",False),SQE("Z",False)]),

            MQE(p_p/15,[SQE("Z",False),SQE("I",False)]),
            MQE(p_p/15,[SQE("Z",False),SQE("X",False)]),
            MQE(p_p/15,[SQE("Z",False),SQE("Y",False)]),
            MQE(p_p/15,[SQE("Z",False),SQE("Z",False)]),
        ]
    )


def get_2q_biased_erasure_mechanism(p_e):
    return Error_mechanism(
    [   
        MQE((1- p_e)**2,[SQE("I",False),SQE("I",False)]), # no detection cases

        MQE(p_e/2 * (1- p_e),[SQE("I",True),SQE("I",False)]), # Single qubit detection cases
        MQE(p_e/2 * (1- p_e),[SQE("Z",True),SQE("I",False)]),
        MQE((1- p_e) * p_e/2,[SQE("I",False),SQE("I",True)]),
        MQE((1- p_e) * p_e/2,[SQE("I",False),SQE("Z",True)]),

        MQE( (p_e/2)**2,[SQE("I",True),SQE("I",True)]), # Two qubit detection cases
        MQE( (p_e/2)**2,[SQE("I",True),SQE("Z",True)]),
        MQE( (p_e/2)**2,[SQE("Z",True),SQE("I",True)]),
        MQE( (p_e/2)**2,[SQE("Z",True),SQE("Z",True)]),
    ]
)

def get_2q_differential_shift_mechanism(p_z_shift):
    return Error_mechanism(
    [   
        MQE((1-p_z_shift)**2,[SQE("I",False),SQE("I",False)]),
        MQE(p_z_shift * (1-p_z_shift),[SQE("Z",False),SQE("I",False)]),
        MQE(p_z_shift * (1-p_z_shift),[SQE("I",False),SQE("Z",False)]),
        MQE(p_z_shift**2,[SQE("Z",False),SQE("Z",False)]),
    ]
)

def get_2q_error_model(p_e,p_z_shift, p_p):
    mechanism_list = [get_2q_depolarization_mechanism(p_p)]
    if p_z_shift>0:
        mechanism_list.append(get_2q_differential_shift_mechanism(p_z_shift))
    if p_e>0:
        mechanism_list.append(get_2q_biased_erasure_mechanism(p_e))
    return Gate_error_model(mechanism_list)




# def product_of_sigma(s1,s2):
#     assert s1 in ['X','Y','Z','I'] and s2 in ['X','Y','Z','I']
#     return {
#         ('I','I'):'I',
#         ('I','X'):'X',
#         ('I','Y'):'Y',
#         ('I','Z'):'Z',

#         ('X','I'):'X',
#         ('X','X'):'I',
#         ('X','Y'):'Z',
#         ('X','Z'):'Y',

#         ('Y','I'):'Y',
#         ('Y','X'):'Z',
#         ('Y','Y'):'I',
#         ('Y','Z'):'X',

#         ('Z','I'):'Z',
#         ('Z','X'):'Y',
#         ('Z','Y'):'X',
#         ('Z','Z'):'I',
#     }[(s1,s2)]

# def error_mechanism_product(m1:Error_mechanism,m2:Error_mechanism) -> Error_mechanism:
#     # Not used anymore, better use a Gate_error_model to represent two independent mechanisms
#     num_qubits = m1.num_qubits
#     assert num_qubits == m2.num_qubits
#     product_list_of_MQE = []
#     for event1 in m1.list_of_MQE:
#         for event2 in m2.list_of_MQE:
#             list_of_SQE = []
#             for i in range(num_qubits):
#                 list_of_SQE.append(SQE(type=product_of_sigma(event1.list_of_SQE[i].type,event2.list_of_SQE[i].type),
#                                        heralded=any([event1.list_of_SQE[i].heralded,event2.list_of_SQE[i].heralded])))
#             product_list_of_MQE.append(MQE(event1.p * event2.p,list_of_SQE) )
#     return Error_mechanism(list_of_MQE=product_list_of_MQE)

