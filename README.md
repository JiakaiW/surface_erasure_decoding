# surface_erasure_decoding
 
The rotated surface code part is derived from [Stim](https://github.com/quantumlib/Stim)'s c++ code

This package help me generate decoding problem istances that I can send to distributed computing nodes.

# How I used this package:
1. use Docker to build a container and store in DockerHub.
2. generate decoding problem instances and send those instances to distributed computing
3. gather those decoding results in form of JSON files
4. data analytics on my local computer


# 1. How direct Monte Carlo sampling is done currently with Sample_decode_job

1. it assembles a easure_circ_builder and prepare it by calling its generate_circuit_and_decoding_info()

2. then it compile a measurement sampler of the erasure_circuit,

3. sample the measurements

4. convert measurements to measurements and detectors

5. for each shot
    1. generate new circuit with posterior probabilities 
    2. use the new circuit to decode that shot


# 2. How did I generate circuit with posterior probabilities?

1. measurement_sample -> gen_dynamic_circuit
    1. sync all Gate_error_model with the same copy of a) erasure_measurement_index b) measurement_sample
    2. when gen_circuit(mode = 'dynamic'), the instruction that's appended to the stim circuit is dynamically generated by those Gate_error_models
        1. Gate_error_model.get_dynamic_instruction_vectorized(qubits) calls Error_mechanism.get_dynamic_instruction_vectorized(qubits)



# 3. What's the workflow with importance sampling?

1. each instance of easure_circ_builder is now initialized with number of 2-qubit depolarization errors and the number of erasure errors. 

2. just like how I used the measurement record which include erasure flags to dynamically control the behavior of Error_mechanism. I can use a pre-computed bit array to control Error_mechanism for the purpose of importance sampling. That is, I add another mode to Error_mechanism.


# 4. future plans

1. once importance sampling is implemented, the same code can be used for simulating imperfect erasure detection and that doesn't require using a TableauSimulator. Basically, I row dices before running the simulator and use the dice results to generate a circuit with error probabilities being 1. running that circuit gives me measurement results. Based on the dice results I generate a decoding circuit with posterior probabilities and convert that to MWPM matching graph. Or I feed the erasure detection results and measurement results to a ML model.
