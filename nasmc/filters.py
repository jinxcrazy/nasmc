import torch
import torch.distributions as D

from typing import Tuple, NamedTuple

from nasmc.models import NonlinearSSMProposal, NonlinearSSM


class SMCResult(NamedTuple):
    trajectories: torch.Tensor
    weights: torch.Tensor
    proposal_log_probs: torch.Tensor


def nonlinear_ssm_smc(proposal: NonlinearSSMProposal, model: NonlinearSSM,
                      observations: torch.Tensor,
                      num_particles: int) -> SMCResult:
    # Dimensionality Analysis
    #
    # Weights: [num_particles, batch_size, num_timesteps, 1]
    # LSTM State: [num_particles, batch_size, hidden_dim]
    # Trajectories: [num_particles, batch_size, num_timesteps, state_dim]
    # Current States: [num_particles, batch_size, state_dim]
    # Current Observations: [num_particles, batch_size, obs_dim]
    # Proposal Log Probabilities: [num_particles, batch_size, num_timesteps, 1]

    batch_size, seq_len, _ = observations.shape

    current_observations = observations[None, :,
                                        0, :].repeat(num_particles, 1, 1)
    proposal_d, lstm_state = proposal.parameterize_prior(current_observations)
    transition_model = model.parameterize_prior_model()
    current_states = proposal_d.sample()

    observation_model = model.parameterize_observation_model(current_states)

    transition_prob = transition_model.log_prob(current_states)
    proposal_prob = proposal_d.log_prob(current_states)[..., None]
    observation_prob = observation_model.log_prob(current_observations)

    weights = transition_prob * observation_prob / proposal_prob
    weights = weights / torch.sum(weights, dim=0, keepdims=True)
    weights = weights[..., None, :]

    proposal_log_probs = proposal_d.log_prob(current_states)
    proposal_log_probs = proposal_log_probs[..., None, None]

    trajectories = current_states[..., None, :]

    for i in range(1, seq_len):
        current_observations = observations[None, :,
                                            i, :].repeat(num_particles, 1, 1)
        ancestor_d = D.Categorical(weights[..., i - 1, 0].permute(1, 0))
        ancestors = ancestor_d.sample((num_particles, ))

        resampled_trajectories = torch.gather(
            trajectories, 0,
            ancestors[..., None, None].repeat(1, 1, trajectories.shape[-2],
                                              trajectories.shape[-1]))
        resampled_weights = torch.gather(
            weights, 0, ancestors[..., None,
                                  None].repeat(1, 1, weights.shape[-2],
                                               weights.shape[-1]))
        resampled_proposal_log_probs = torch.gather(
            proposal_log_probs, 0,
            ancestors[..., None,
                      None].repeat(1, 1, proposal_log_probs.shape[-2],
                                   proposal_log_probs.shape[-1]))

        previous_states = resampled_trajectories[..., i - 1, :]

        proposal_d, lstm_state = proposal.parameterize_posterior(
            previous_states, current_observations, lstm_state)
        transition_model = model.parameterize_transition_model(
            previous_states, i + 1)
        current_states = proposal_d.sample()

        observation_model = model.parameterize_observation_model(
            current_states)

        transition_prob = transition_model.log_prob(current_states)
        proposal_prob = proposal_d.log_prob(current_states)[..., None]
        observation_prob = observation_model.log_prob(current_observations)

        current_weights = transition_prob * observation_prob / proposal_prob
        current_weights = current_weights / torch.sum(
            current_weights, dim=0, keepdims=True)
        current_weights = current_weights[..., None, :]

        current_trajectories = current_states[..., None, :]

        current_proposal_log_probs = proposal_d.log_prob(current_states)
        current_proposal_log_probs = current_proposal_log_probs[..., None,
                                                                None]

        weights = torch.cat([resampled_weights, current_weights], dim=-2)
        trajectories = torch.cat(
            [resampled_trajectories, current_trajectories], dim=-2)
        proposal_log_probs = torch.cat(
            [resampled_proposal_log_probs, current_proposal_log_probs], dim=-2)

    return SMCResult(trajectories, weights, proposal_log_probs)